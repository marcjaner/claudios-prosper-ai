"""Run an isolated, 180-second voice call with a simulated patient."""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import io
import json
import logging
import os
import shutil
import sys
import time
import uuid
import wave
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
import uvicorn
import websockets
from dotenv import load_dotenv
from fake_harness import (
    build_media_message,
    build_start_message,
    read_wav_as_mulaw_source,
)
from loguru import logger
from simulation import (
    IMPORTABLE_PUBLIC_PROBLEMS,
    CallerReply,
    LocalClinicApi,
    caller_profile,
    caller_prompt,
    load_case,
    outcome_matches,
    public_cases,
)

CALL_SECONDS = 180
FRAME_SECONDS = 0.02
FRAME_BYTES = 160
SILENCE = b"\xff" * FRAME_BYTES
SPEECH_RMS = 100
REPLY_SILENCE_SECONDS = 1.0
LOCAL_SILENCE_TIMEOUT = 30


def wav_bytes(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(pcm)
    return buffer.getvalue()


async def command(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace").strip())
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def speak_file(text: str, voice: str, path: Path) -> bytes:
    path.with_suffix(".txt").write_text(text)
    await command(
        "say",
        "-v",
        voice,
        "-r",
        "165",
        "-f",
        str(path.with_suffix(".txt")),
        "-o",
        str(path.with_suffix(".aiff")),
    )
    await command(
        "ffmpeg",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(path.with_suffix(".aiff")),
        "-ar",
        "8000",
        "-ac",
        "1",
        str(path),
    )
    return read_wav_as_mulaw_source(path)


class Phone:
    """A real-time Twilio-shaped phone peer; only received audio informs the caller."""

    def __init__(self, websocket, stream_id: str):
        self.websocket = websocket
        self.stream_id = stream_id
        self.sequence = 1
        self.playback: deque[bytes] = deque()
        self.playback_done = asyncio.Event()
        self.playback_done.set()
        self.received: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.last_voice = 0.0
        self.audio_received = 0

    async def send_audio(self):
        next_frame = time.monotonic()
        while True:
            frame = self.playback.popleft() if self.playback else SILENCE
            self.sequence += 1
            await self.websocket.send(
                json.dumps(build_media_message(self.stream_id, self.sequence, frame))
            )
            if not self.playback:
                self.playback_done.set()
            next_frame += FRAME_SECONDS
            await asyncio.sleep(max(0, next_frame - time.monotonic()))

    async def receive_audio(self):
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if message.get("event") != "media":
                    continue
                pcm = audioop.ulaw2lin(base64.b64decode(message["media"]["payload"]), 2)
                self.audio_received += len(pcm)
                if audioop.rms(pcm, 2) > SPEECH_RMS:
                    self.last_voice = time.monotonic()
                self.received.put_nowait(pcm)
        finally:
            self.received.put_nowait(None)

    async def hear(self) -> bytes:
        chunks = []
        heard_voice = False
        started = time.monotonic()
        while True:
            try:
                pcm = await asyncio.wait_for(self.received.get(), 0.1)
            except TimeoutError:
                pcm = b""
            if pcm is None:
                raise ConnectionError("Agent closed the local call")
            if pcm:
                chunks.append(pcm)
                heard_voice = heard_voice or audioop.rms(pcm, 2) > SPEECH_RMS
            if (
                heard_voice
                and self.received.empty()
                and time.monotonic() - self.last_voice >= REPLY_SILENCE_SECONDS
            ):
                return b"".join(chunks)
            if not heard_voice and time.monotonic() - started >= LOCAL_SILENCE_TIMEOUT:
                raise TimeoutError(
                    "No audible agent reply for 30 seconds (local heuristic)"
                )

    async def say(self, pcm: bytes):
        # Normal scenarios wait for the agent to finish; forced overlap is a
        # separate scenario rather than an artifact of local TTS preparation.
        while time.monotonic() - self.last_voice < REPLY_SILENCE_SECONDS:
            await asyncio.sleep(0.1)
        mulaw = audioop.lin2ulaw(pcm, 2)
        self.playback_done.clear()
        self.playback.extend(
            mulaw[offset : offset + FRAME_BYTES].ljust(FRAME_BYTES, b"\xff")
            for offset in range(0, len(mulaw), FRAME_BYTES)
        )
        await self.playback_done.wait()


async def transcribe(client: httpx.AsyncClient, pcm: bytes) -> str:
    response = await client.post(
        "https://api.deepgram.com/v1/listen",
        params={"model": "nova-3", "language": "multi", "smart_format": "true"},
        headers={
            "Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}",
            "Content-Type": "audio/wav",
        },
        content=wav_bytes(pcm),
    )
    response.raise_for_status()
    return response.json()["results"]["channels"][0]["alternatives"][0][
        "transcript"
    ].strip()


async def conversation(phone: Phone, profile: dict, report: dict):
    from agent.llm import get_llm_client

    caller = get_llm_client()
    directory = Path(report["directory"])
    history = []
    async with httpx.AsyncClient(timeout=15) as client:
        for index in range(40):
            pcm = await phone.hear()
            (directory / f"heard-{index:02}.wav").write_bytes(wav_bytes(pcm))
            heard = await transcribe(client, pcm)
            if not heard:
                continue
            row = {
                "speaker": "agent",
                "text": heard,
                "seconds": round(time.monotonic() - report["started"], 2),
            }
            report["transcript"].append(row)
            history.append({"speaker": "receptionist", "text": heard})
            print(f"AGENT: {heard}", flush=True)
            reply = await asyncio.to_thread(
                caller.complete_structured, caller_prompt(profile, history), CallerReply
            )
            decision = reply.data
            if not isinstance(decision, CallerReply):
                raise TypeError("Caller model returned an invalid reply")
            if decision.action == "hangup":
                return "caller_hangup"
            if decision.action == "wait":
                continue
            if not decision.text.strip():
                raise ValueError("Caller model returned an empty spoken reply")
            pcm = await speak_file(
                decision.text,
                profile.get("voice", "Samantha"),
                directory / f"caller-{index:02}.wav",
            )
            history.append({"speaker": "you", "text": decision.text})
            report["transcript"].append(
                {
                    "speaker": "caller",
                    "text": decision.text,
                    "seconds": round(time.monotonic() - report["started"], 2),
                }
            )
            print(f"CALLER: {decision.text}", flush=True)
            await phone.say(pcm)
    return "turn_limit"


async def dial(url: str, profile: dict, api: LocalClinicApi, report: dict):
    stream_id = f"MZ{uuid.uuid4().hex}"
    async with websockets.connect(url, open_timeout=10, close_timeout=2) as websocket:
        phone = Phone(websocket, stream_id)
        api.start_call(report["call_id"], report["limit_seconds"])
        report["started"] = api.started_at
        await websocket.send(
            json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        )
        await websocket.send(
            json.dumps(
                build_start_message(
                    stream_id, report["call_id"], profile.get("from_number")
                )
            )
        )
        sender = asyncio.create_task(phone.send_audio())
        receiver = asyncio.create_task(phone.receive_audio())
        try:
            async with asyncio.timeout(report["limit_seconds"]):
                report["ending"] = await conversation(phone, profile, report)
        except TimeoutError as error:
            report["ending"] = (
                "deadline" if time.monotonic() >= api.deadline else "silence"
            )
            report["error"] = str(error) or "Call reached its hard time limit"
        except Exception as error:  # noqa: BLE001 - retain a diagnostic for a failed simulation
            report["ending"] = "error"
            report["error"] = f"{type(error).__name__}: {error}"
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError, websockets.ConnectionClosed):
                await sender
            report["duration_seconds"] = round(time.monotonic() - api.started_at, 2)
            report["agent_audio_seconds"] = round(phone.audio_received / 16000, 2)
            with suppress(websockets.ConnectionClosed):
                await websocket.send(
                    json.dumps(
                        {
                            "event": "stop",
                            "sequenceNumber": str(phone.sequence + 1),
                            "streamSid": stream_id,
                            "stop": {"callSid": report["call_id"]},
                        }
                    )
                )
                await websocket.close()
            with suppress(websockets.ConnectionClosed):
                await receiver


async def simulate(case: dict, directory: Path, seconds: float) -> dict:
    # Set all runtime paths before importing the app. This process never uses
    # the live server, its SQLite files, its endpoint, or its recorded calls.
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{directory / 'agent.db'}"
    os.environ["CALLS_DB"] = str(directory / "calls.db")
    os.environ["CALL_RECORDINGS_DIR"] = str(directory / "recordings")
    graph_path = Path(os.getenv("AGENT_GRAPH_PATH", ROOT / "graphs" / "default.json"))
    shutil.copyfile(graph_path, directory / "graph.json")
    os.environ["AGENT_GRAPH_PATH"] = str(directory / "graph.json")
    from agent import reply
    from agent.clinic_api import ClinicApi
    from agent.transcription import INITIAL_GREETING, create_transcription_agent
    from storage import Database
    from twilio.server import create_app

    api = LocalClinicApi(
        os.environ["PLATFORM_API_BASE_URL"], os.environ["PLATFORM_API_KEY"]
    )
    profile = await asyncio.to_thread(caller_profile, case, api)
    with (
        patch.object(reply, "CALL_LIMIT_SECONDS", seconds),
        patch.object(ClinicApi, "from_environment", classmethod(lambda cls: api)),
    ):
        database = Database()
        app = create_app(
            create_transcription_agent(database=database),
            initial_greeting=INITIAL_GREETING,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=5,
            )
        )
        task = asyncio.create_task(server.serve())
        report = {
            "case_id": case["id"],
            "call_id": f"LOCAL{uuid.uuid4().hex}",
            "directory": str(directory),
            "limit_seconds": seconds,
            "transcript": [],
            "accepted": case["accepted"],
            "error": None,
            "started_at": datetime.now(UTC).isoformat(),
        }
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("Local server exited before startup")
                    await asyncio.sleep(0.05)
            port = server.servers[0].sockets[0].getsockname()[1]
            print(
                f"Local call: {case['id']} | limit {seconds:g}s | ws://127.0.0.1:{port}/ws",
                flush=True,
            )
            await dial(f"ws://127.0.0.1:{port}/ws", profile, api, report)
        finally:
            server.should_exit = True
            await task
            await database.close()
            api.shutdown()
        report.pop("started", None)
        report["actions"] = api.actions
        report["submission_attempts"] = api.attempts
        report["record_matches"] = outcome_matches(api.actions, case["accepted"])
        report["completed_conversation"] = report["ending"] == "caller_hangup"
        report["passed"] = report["record_matches"]
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, nargs="?", help="YAML scenario")
    parser.add_argument("--public-case", help="ID from the cached public cases")
    parser.add_argument("--list-public", action="store_true")
    parser.add_argument(
        "--seconds", type=float, default=CALL_SECONDS, help="Call limit, at most 180"
    )
    parser.add_argument("--out", type=Path, help="New output directory")
    args = parser.parse_args()
    if args.list_public:
        for case in public_cases():
            mode = (
                "ready"
                if case["case_id"].rsplit("-", 1)[0] in IMPORTABLE_PUBLIC_PROBLEMS
                else "needs YAML persona"
            )
            print(f"{case['case_id']}  [{mode}]  {case['caller']}  {case['summary']}")
        return 0
    if not 0 < args.seconds <= CALL_SECONDS:
        parser.error("--seconds must be greater than zero and at most 180")
    if bool(args.case) == bool(args.public_case):
        parser.error("Choose either a YAML case or --public-case ID")
    for executable in ("say", "ffmpeg"):
        if not shutil.which(executable):
            parser.error(
                f"{executable} is required (the caller voice currently uses macOS say)"
            )
    load_dotenv(ROOT / ".env")
    case = load_case(args.case, args.public_case)
    directory = (
        args.out
        or ROOT
        / "outputs"
        / "simulations"
        / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    ).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n"
    )
    logger.remove()
    logger.add(directory / "server.log", level="INFO")
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(directory / "server.log")],
        force=True,
    )
    report = asyncio.run(simulate(case, directory, args.seconds))
    print(
        f"\nRECORD {'MATCH' if report['record_matches'] else 'MISMATCH'} | {report['duration_seconds']}s | {report['ending']}"
    )
    print("Submitted:", json.dumps(report["actions"], ensure_ascii=False))
    if not report["record_matches"]:
        print("Expected:", json.dumps(report["accepted"], ensure_ascii=False))
    print(f"Report: {directory / 'report.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
