import asyncio
from types import SimpleNamespace

from pipecat.audio.turn.base_turn_analyzer import (
    BaseTurnAnalyzer,
    BaseTurnParams,
    EndOfTurnState,
)
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TurnMetricsData
from pipecat.processors.frame_processor import FrameDirection

from stt.deepgram import (
    DeepgramEndpointingStopStrategy,
    DeepgramEOTCoordinator,
    DeepgramEOTEventFrame,
)
from twilio.recording import CallTimelineObserver


class FakeSmartTurn(BaseTurnAnalyzer):
    def __init__(self, probability: float):
        super().__init__(sample_rate=16_000)
        self.probability = probability
        self.audio_states: list[bool] = []
        self.analyzed_audio_states: list[list[bool]] = []

    @property
    def speech_triggered(self) -> bool:
        return any(self.audio_states)

    @property
    def params(self) -> BaseTurnParams:
        return BaseTurnParams()

    def append_audio(self, buffer: bytes, is_speech: bool) -> EndOfTurnState:
        self.audio_states.append(is_speech)
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self):
        self.analyzed_audio_states.append(self.audio_states.copy())
        is_complete = self.probability > 0.5
        state = EndOfTurnState.COMPLETE if is_complete else EndOfTurnState.INCOMPLETE
        metrics = TurnMetricsData(
            processor="FakeSmartTurn",
            is_complete=is_complete,
            probability=self.probability,
            e2e_processing_time_ms=1,
        )
        return state, metrics

    def clear(self):
        self.audio_states.clear()


class CapturingEOTCoordinator(DeepgramEOTCoordinator):
    def __init__(self, smart_turn: BaseTurnAnalyzer, **kwargs):
        super().__init__(smart_turn, **kwargs)
        self.emitted = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.emitted.append(frame)


class CapturingEndpointingStopStrategy(DeepgramEndpointingStopStrategy):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def trigger_user_turn_stopped(self, *, enable_user_speaking_frames=None):
        self.calls.append(enable_user_speaking_frames)


def speech_final(text: str = "Quiero pedir una cita") -> TranscriptionFrame:
    return TranscriptionFrame(
        text,
        "caller",
        "2026-09-19T00:00:00Z",
        result=SimpleNamespace(speech_final=True),
    )


def test_smart_turn_complete_commits_after_grace_period():
    async def run():
        smart_turn = FakeSmartTurn(probability=0.9)
        coordinator = CapturingEOTCoordinator(smart_turn)
        before_speech = InputAudioRawFrame(b"\0\0", 16_000, 1)
        during_speech = InputAudioRawFrame(b"\1\0", 16_000, 1)
        transcript = speech_final()

        await coordinator.process_frame(before_speech, FrameDirection.DOWNSTREAM)
        await coordinator.process_frame(
            VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await coordinator.process_frame(during_speech, FrameDirection.DOWNSTREAM)
        await coordinator.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await coordinator.process_frame(transcript, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.8)
        return transcript, smart_turn.analyzed_audio_states, coordinator.emitted

    transcript, analyzed_audio_states, emitted = asyncio.run(run())

    assert transcript.finalized is True
    assert analyzed_audio_states == [[False, True]]
    assert any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)
    assert emitted[-1] is transcript
    decisions = [
        frame.decision for frame in emitted if isinstance(frame, DeepgramEOTEventFrame)
    ]
    assert decisions == ["candidate", "committed"]


def test_smart_turn_incomplete_vetoes_deepgram_candidate():
    async def run():
        coordinator = CapturingEOTCoordinator(
            FakeSmartTurn(probability=0.5), incomplete_timeout_seconds=0.1
        )
        transcript = speech_final("Quiero pedir")
        await coordinator.process_frame(transcript, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.01)
        return transcript, coordinator.emitted

    transcript, emitted = asyncio.run(run())

    assert transcript.finalized is False
    assert not any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)
    decision = emitted[-1]
    assert isinstance(decision, DeepgramEOTEventFrame)
    assert decision.smart_turn_probability == 0.5
    assert decision.decision == "deferred"
    assert decision.cancellation_reason == "smart_turn_incomplete"


def test_smart_turn_incomplete_commits_after_bounded_timeout():
    async def run():
        coordinator = CapturingEOTCoordinator(
            FakeSmartTurn(probability=0.2), incomplete_timeout_seconds=0.02
        )
        transcript = speech_final("Quiero una cita")
        await coordinator.process_frame(transcript, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.03)
        return transcript, coordinator.emitted

    transcript, emitted = asyncio.run(run())

    assert transcript.finalized is True
    assert any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)
    decisions = [
        frame.decision for frame in emitted if isinstance(frame, DeepgramEOTEventFrame)
    ]
    assert decisions == ["candidate", "deferred", "committed"]


def test_smart_turn_incomplete_still_cancels_when_speech_resumes():
    async def run():
        coordinator = CapturingEOTCoordinator(
            FakeSmartTurn(probability=0.2), incomplete_timeout_seconds=0.05
        )
        transcript = speech_final("Quiero pedir")
        await coordinator.process_frame(transcript, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.01)
        await coordinator.process_frame(
            VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await asyncio.sleep(0.06)
        return transcript, coordinator.emitted

    transcript, emitted = asyncio.run(run())

    assert transcript.finalized is False
    assert not any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)
    decisions = [
        frame.decision for frame in emitted if isinstance(frame, DeepgramEOTEventFrame)
    ]
    assert decisions == ["candidate", "deferred", "cancelled"]


def test_smart_turn_cannot_stop_without_deepgram_candidate():
    async def run():
        smart_turn = FakeSmartTurn(probability=0.9)
        coordinator = CapturingEOTCoordinator(smart_turn)
        await coordinator.process_frame(
            VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await coordinator.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await asyncio.sleep(0.8)
        return smart_turn.analyzed_audio_states, coordinator.emitted

    analyzed_audio_states, emitted = asyncio.run(run())

    assert analyzed_audio_states == []
    assert not any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)


def test_vad_resume_cancels_pending_turn_stop():
    async def run():
        coordinator = CapturingEOTCoordinator(FakeSmartTurn(probability=0.9))
        transcript = speech_final("Quiero pedir")
        resumed = VADUserStartedSpeakingFrame()
        await coordinator.process_frame(transcript, FrameDirection.DOWNSTREAM)
        await coordinator.process_frame(resumed, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.8)
        return transcript, resumed, coordinator.emitted

    transcript, resumed, emitted = asyncio.run(run())

    assert transcript.finalized is False
    assert not any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)
    assert emitted[-2:] == [transcript, resumed]
    cancellation = emitted[-3]
    assert isinstance(cancellation, DeepgramEOTEventFrame)
    assert cancellation.vad_state == "speaking"
    assert cancellation.cancellation_reason == "vad_resumed"


def test_new_transcript_cancels_pending_turn_stop():
    async def run():
        coordinator = CapturingEOTCoordinator(FakeSmartTurn(probability=0.9))
        endpoint = speech_final("Quiero pedir")
        continuation = InterimTranscriptionFrame(
            "Quiero pedir una cita",
            "caller",
            "2026-09-19T00:00:00Z",
        )
        await coordinator.process_frame(endpoint, FrameDirection.DOWNSTREAM)
        await coordinator.process_frame(continuation, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.8)
        return endpoint, continuation, coordinator.emitted

    endpoint, continuation, emitted = asyncio.run(run())

    assert endpoint.finalized is False
    assert not any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)
    assert emitted[-2:] == [endpoint, continuation]
    cancellation = emitted[-3]
    assert isinstance(cancellation, DeepgramEOTEventFrame)
    assert cancellation.cancellation_reason == "new_transcript"


def test_segment_final_after_vad_stops_can_end_turn():
    async def run():
        coordinator = CapturingEOTCoordinator(FakeSmartTurn(probability=0.9))
        transcript = TranscriptionFrame(
            "Quiero pedir",
            "caller",
            "2026-09-19T00:00:00Z",
            result=SimpleNamespace(speech_final=False),
        )
        await coordinator.process_frame(
            VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await coordinator.process_frame(transcript, FrameDirection.DOWNSTREAM)
        await coordinator.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await asyncio.sleep(0.8)
        return transcript, coordinator.emitted

    transcript, emitted = asyncio.run(run())

    assert transcript.finalized is True
    assert sum(frame is transcript for frame in emitted) == 1
    assert any(isinstance(frame, UserStoppedSpeakingFrame) for frame in emitted)
    commit = next(
        frame
        for frame in emitted
        if isinstance(frame, DeepgramEOTEventFrame) and frame.decision == "committed"
    )
    assert commit.deepgram_event == "is_final"


def test_segment_final_without_vad_does_not_create_candidate():
    async def run():
        coordinator = CapturingEOTCoordinator(FakeSmartTurn(probability=0.9))
        transcript = TranscriptionFrame(
            "Background audio",
            "caller",
            "2026-09-19T00:00:00Z",
            result=SimpleNamespace(speech_final=False),
        )
        await coordinator.process_frame(transcript, FrameDirection.DOWNSTREAM)
        return transcript, coordinator.emitted

    transcript, emitted = asyncio.run(run())

    assert transcript.finalized is False
    assert emitted == [transcript]


def test_eot_event_is_recorded_in_call_timeline():
    event = CallTimelineObserver._event_for_frame(
        DeepgramEOTEventFrame(
            transcript="Quiero pedir",
            vad_state="quiet",
            decision="cancelled",
            smart_turn_probability=0.2,
            cancellation_reason="smart_turn_incomplete",
        ),
        FrameDirection.DOWNSTREAM,
    )

    assert event == {
        "event": "eot_decision",
        "transcript": "Quiero pedir",
        "deepgram_event": "speech_final",
        "vad_state": "quiet",
        "smart_turn_probability": 0.2,
        "decision": "cancelled",
        "cancellation_reason": "smart_turn_incomplete",
    }


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
