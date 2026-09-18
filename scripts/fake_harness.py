"""Stand-in for the El Turno harness: dials our WebSocket and plays a caller."""

import argparse
import asyncio
import audioop
import base64
import json
import math
import struct
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

import websockets

WIRE_SAMPLE_RATE = 8_000
FRAME_DURATION_MS = 20
MULAW_FRAME_BYTES = 160
MULAW_SILENCE = b"\xff"


def read_wav_as_mulaw_source(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        channels, width, rate = (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
        )
        pcm = wav.readframes(wav.getnframes())

    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if channels > 1:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != WIRE_SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, WIRE_SAMPLE_RATE, None)
    return pcm


def synthesize_caller_audio(seconds: float) -> bytes:
    # Not speech, just an audible non-constant signal, so the tool needs no
    # audio assets to prove the path carries a real waveform.
    total = int(WIRE_SAMPLE_RATE * seconds)
    samples = []
    for index in range(total):
        t = index / WIRE_SAMPLE_RATE
        value = 0.45 * math.sin(2 * math.pi * 220 * t) + 0.25 * math.sin(
            2 * math.pi * 740 * t
        )
        value *= 0.5 + 0.5 * math.sin(2 * math.pi * 1.7 * t)
        samples.append(int(max(-1.0, min(1.0, value)) * 20_000))
    return struct.pack(f"<{total}h", *samples)


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(WIRE_SAMPLE_RATE)
        wav.writeframes(pcm)


@dataclass
class CallResult:
    call_id: str
    ok: bool = False
    error: str | None = None
    frames_sent: int = 0
    frames_received: int = 0
    events: dict[str, int] = field(default_factory=dict)
    first_reply_seconds: float | None = None
    reply_seconds: float = 0.0
    reply_span_seconds: float = 0.0
    pcm_received: bytes = b""

    @property
    def realtime_factor(self) -> float | None:
        if self.reply_span_seconds <= 0 or self.reply_seconds <= 0:
            return None
        return round(self.reply_seconds / self.reply_span_seconds, 3)

    def summary(self) -> dict:
        return {
            "call_id": self.call_id,
            "ok": self.ok,
            "error": self.error,
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "events": self.events,
            "first_reply_seconds": (
                None
                if self.first_reply_seconds is None
                else round(self.first_reply_seconds, 3)
            ),
            "reply_seconds": round(self.reply_seconds, 3),
            "realtime_factor": self.realtime_factor,
        }


def build_start_message(
    stream_sid: str, call_sid: str, from_number: str | None
) -> dict:
    parameters = {"call_id": call_sid}
    if from_number is not None:
        # Absent, not empty, is how a withheld caller id arrives.
        parameters["from_number"] = from_number

    return {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": stream_sid,
        "start": {
            "streamSid": stream_sid,
            "accountSid": "ACfake00000000000000000000000000",
            "callSid": call_sid,
            "tracks": ["inbound"],
            "customParameters": parameters,
            "mediaFormat": {
                "encoding": "audio/x-mulaw",
                "sampleRate": WIRE_SAMPLE_RATE,
                "channels": 1,
            },
        },
    }


def build_media_message(stream_sid: str, sequence: int, frame: bytes) -> dict:
    # sequenceNumber, chunk and timestamp are strings on the wire, and every
    # key is camelCase. A server that only handles tidier JSON fails here.
    return {
        "event": "media",
        "sequenceNumber": str(sequence),
        "streamSid": stream_sid,
        "media": {
            "track": "inbound",
            "chunk": str(sequence - 1),
            "timestamp": str((sequence - 2) * FRAME_DURATION_MS),
            "payload": base64.b64encode(frame).decode("ascii"),
        },
    }


async def collect_replies(websocket, result: CallResult, started: float) -> None:
    chunks: list[bytes] = []
    first_at: float | None = None
    last_at: float | None = None

    try:
        async for raw in websocket:
            message = json.loads(raw)
            event = message.get("event")
            if event != "media":
                result.events[event] = result.events.get(event, 0) + 1
                continue

            payload = message.get("media", {}).get("payload")
            if not payload:
                continue

            chunks.append(audioop.ulaw2lin(base64.b64decode(payload), 2))
            last_at = time.monotonic()
            if first_at is None:
                first_at = last_at
                result.first_reply_seconds = last_at - started
            result.frames_received += 1
    except websockets.exceptions.ConnectionClosed:
        pass

    result.pcm_received = b"".join(chunks)
    result.reply_seconds = len(result.pcm_received) / (WIRE_SAMPLE_RATE * 2)
    if first_at is not None and last_at is not None:
        result.reply_span_seconds = max(last_at - first_at, 1e-9)


async def place_call(
    url: str,
    pcm: bytes,
    from_number: str | None = "+34612345678",
    linger_seconds: float = 3.0,
) -> CallResult:
    call_sid = f"CA{uuid.uuid4().hex}"
    stream_sid = f"MZ{uuid.uuid4().hex}"
    result = CallResult(call_id=call_sid)
    started = time.monotonic()

    try:
        async with websockets.connect(
            url, open_timeout=10, close_timeout=5
        ) as websocket:
            reader = asyncio.create_task(collect_replies(websocket, result, started))

            await websocket.send(
                json.dumps(
                    {"event": "connected", "protocol": "Call", "version": "1.0.0"}
                )
            )
            await websocket.send(
                json.dumps(build_start_message(stream_sid, call_sid, from_number))
            )

            mulaw = audioop.lin2ulaw(pcm, 2)
            frame_period = FRAME_DURATION_MS / 1000
            next_send = time.monotonic()
            sequence = 1

            for offset in range(0, len(mulaw), MULAW_FRAME_BYTES):
                frame = mulaw[offset : offset + MULAW_FRAME_BYTES]
                frame += MULAW_SILENCE * (MULAW_FRAME_BYTES - len(frame))
                sequence += 1
                await websocket.send(
                    json.dumps(build_media_message(stream_sid, sequence, frame))
                )
                result.frames_sent += 1

                # Absolute schedule, so send time does not accumulate into
                # drift over a three minute call.
                next_send += frame_period
                delay = next_send - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)

            await asyncio.sleep(linger_seconds)
            await websocket.send(
                json.dumps(
                    {
                        "event": "stop",
                        "sequenceNumber": str(sequence + 1),
                        "streamSid": stream_sid,
                        "stop": {"callSid": call_sid},
                    }
                )
            )

        await asyncio.wait_for(reader, timeout=5)
        result.ok = True
    except Exception as error:  # noqa: BLE001 - reporting the failure is the point
        result.error = f"{type(error).__name__}: {error}"

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:7860/ws")
    parser.add_argument(
        "--wav", help="caller audio; a synthesized sweep is used without it"
    )
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--out", help="write the reply audio here")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--linger", type=float, default=3.0)
    parser.add_argument("--withhold-caller-id", action="store_true")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    pcm = (
        read_wav_as_mulaw_source(Path(args.wav))
        if args.wav
        else synthesize_caller_audio(args.seconds)
    )
    from_number = None if args.withhold_caller_id else "+34612345678"

    results = await asyncio.gather(
        *(
            place_call(args.url, pcm, from_number, args.linger)
            for _ in range(args.concurrency)
        )
    )

    for result in results:
        print(json.dumps(result.summary()))

    if args.out:
        for index, result in enumerate(results):
            if not result.pcm_received:
                continue
            path = Path(args.out)
            if len(results) > 1:
                path = path.with_name(f"{path.stem}-{index:02d}{path.suffix}")
            write_wav(path, result.pcm_received)
            print(f"wrote {path}")

    failed = [r for r in results if not r.ok]
    # Silence is attributed to the agent and fails the case, so it is not a
    # softer outcome than an error.
    silent = [r for r in results if r.ok and r.frames_received == 0]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} completed, {len(silent)} silent"
    )
    for result in failed + silent:
        print(f"  {result.call_id}: {result.error or 'no audio came back'}")

    return 1 if failed or silent else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
