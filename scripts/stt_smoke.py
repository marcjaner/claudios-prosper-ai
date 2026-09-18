import argparse
import asyncio
import base64
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pipecat.frames.frames import (
    DataFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.workers.runner import WorkerRunner

from stt import create_deepgram_stt

FRAME_DURATION_SECONDS = 0.02
MULAW_FRAME_BYTES = 160
PIPELINE_SAMPLE_RATE = 16_000
SILENCE_FRAME_COUNT = 50
STREAM_SID = "stt-smoke-test"


@dataclass
class TwilioMediaMessageFrame(DataFrame):
    message: dict[str, object]


class TwilioMediaDecoder(FrameProcessor):
    def __init__(self):
        super().__init__()
        params = TwilioFrameSerializer.InputParams(auto_hang_up=False)
        self._serializer = TwilioFrameSerializer(STREAM_SID, params=params)

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        await self._serializer.setup(setup)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, TwilioMediaMessageFrame):
            await self.push_frame(frame, direction)
            return

        decoded = await self._serializer.deserialize(json.dumps(frame.message))
        if decoded:
            await self.push_frame(decoded, direction)


class TranscriptPrinter(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.final_transcripts: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterimTranscriptionFrame):
            print(f"[interim] {frame.text}")
        elif isinstance(frame, TranscriptionFrame):
            print(f"[final] {frame.text}")
            self.final_transcripts.append(frame.text)
        await self.push_frame(frame, direction)


def transcode_to_mulaw(audio_path: Path) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "8000",
            "-acodec",
            "pcm_mulaw",
            "-f",
            "mulaw",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout


def create_media_message(payload: bytes, sequence: int) -> TwilioMediaMessageFrame:
    message = {
        "event": "media",
        "sequenceNumber": str(sequence),
        "streamSid": STREAM_SID,
        "media": {
            "track": "inbound",
            "chunk": str(sequence),
            "timestamp": str(sequence * 20),
            "payload": base64.b64encode(payload).decode(),
        },
    }
    return TwilioMediaMessageFrame(message)


async def stream_audio(
    worker: PipelineWorker, pipeline_started: asyncio.Event, mulaw_audio: bytes
):
    await pipeline_started.wait()
    padded_audio = mulaw_audio + b"\xff" * MULAW_FRAME_BYTES * SILENCE_FRAME_COUNT
    for sequence, offset in enumerate(
        range(0, len(padded_audio), MULAW_FRAME_BYTES), 1
    ):
        payload = padded_audio[offset : offset + MULAW_FRAME_BYTES]
        payload = payload.ljust(MULAW_FRAME_BYTES, b"\xff")
        await worker.queue_frame(create_media_message(payload, sequence))
        await asyncio.sleep(FRAME_DURATION_SECONDS)
    await worker.queue_frame(EndFrame())


async def run_smoke_test(audio_path: Path):
    mulaw_audio = transcode_to_mulaw(audio_path)
    transcript_printer = TranscriptPrinter()
    pipeline = Pipeline(
        [TwilioMediaDecoder(), create_deepgram_stt(), transcript_printer]
    )
    worker = PipelineWorker(
        pipeline,
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        params=PipelineParams(audio_in_sample_rate=PIPELINE_SAMPLE_RATE),
    )
    pipeline_started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(worker: PipelineWorker, frame: Frame):
        pipeline_started.set()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await asyncio.gather(
        runner.run(), stream_audio(worker, pipeline_started, mulaw_audio)
    )

    if not transcript_printer.final_transcripts:
        raise RuntimeError("Deepgram returned no final transcript")


def main():
    parser = argparse.ArgumentParser(
        description="Stream local audio through Twilio and Deepgram"
    )
    parser.add_argument("audio_file", type=Path)
    args = parser.parse_args()
    if not args.audio_file.is_file():
        parser.error(f"audio file not found: {args.audio_file}")

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    asyncio.run(run_smoke_test(args.audio_file))


if __name__ == "__main__":
    main()
