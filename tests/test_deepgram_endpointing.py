import asyncio
from types import SimpleNamespace

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from stt.deepgram import DeepgramEndpointingProcessor, DeepgramEndpointingStopStrategy


class CapturingEndpointingProcessor(DeepgramEndpointingProcessor):
    def __init__(self):
        super().__init__()
        self.emitted = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.emitted.append(frame)


class CapturingEndpointingStopStrategy(DeepgramEndpointingStopStrategy):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def trigger_user_turn_stopped(self, *, enable_user_speaking_frames=None):
        self.calls.append(enable_user_speaking_frames)


def test_speech_final_completes_the_user_turn():
    async def run():
        processor = CapturingEndpointingProcessor()
        transcript = TranscriptionFrame(
            "Quiero pedir una cita",
            "caller",
            "2026-09-19T00:00:00Z",
            result=SimpleNamespace(speech_final=True),
        )
        await processor.process_frame(transcript, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.8)
        return transcript, processor.emitted

    transcript, emitted = asyncio.run(run())

    assert transcript.finalized is True
    assert isinstance(emitted[0], UserStoppedSpeakingFrame)
    assert emitted[1] is transcript


def test_new_transcript_cancels_pending_turn_stop():
    async def run():
        processor = CapturingEndpointingProcessor()
        endpoint = TranscriptionFrame(
            "Quiero pedir",
            "caller",
            "2026-09-19T00:00:00Z",
            result=SimpleNamespace(speech_final=True),
        )
        continuation = InterimTranscriptionFrame(
            "Quiero pedir una cita",
            "caller",
            "2026-09-19T00:00:00Z",
        )
        await processor.process_frame(endpoint, FrameDirection.DOWNSTREAM)
        await processor.process_frame(continuation, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.8)
        return endpoint, continuation, processor.emitted

    endpoint, continuation, emitted = asyncio.run(run())

    assert endpoint.finalized is False
    assert emitted == [endpoint, continuation]


def test_segment_final_does_not_complete_the_user_turn():
    async def run():
        processor = CapturingEndpointingProcessor()
        transcript = TranscriptionFrame(
            "Quiero pedir",
            "caller",
            "2026-09-19T00:00:00Z",
            result=SimpleNamespace(speech_final=False),
        )
        await processor.process_frame(transcript, FrameDirection.DOWNSTREAM)
        return transcript, processor.emitted

    transcript, emitted = asyncio.run(run())

    assert transcript.finalized is False
    assert emitted == [transcript]


def test_finalized_transcript_triggers_normal_turn_stop():
    async def run():
        strategy = CapturingEndpointingStopStrategy()
        transcript = TranscriptionFrame(
            "Quiero pedir una cita",
            "caller",
            "2026-09-19T00:00:00Z",
        )
        transcript.finalized = True
        await strategy.process_frame(transcript)
        return strategy.calls

    calls = asyncio.run(run())

    assert calls == [False]
