import asyncio
import os
from collections import deque
from dataclasses import dataclass

from loguru import logger
from pipecat.audio.turn.base_turn_analyzer import BaseTurnAnalyzer, EndOfTurnState
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
    LocalSmartTurnAnalyzerV3,
)
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    SystemFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.base_user_turn_stop_strategy import (
    BaseUserTurnStopStrategy,
)

DEEPGRAM_DISCONNECT_TIMEOUT_SECONDS = 2
DEEPGRAM_MODEL = "nova-3-general"
DEEPGRAM_ENDPOINTING_MS = 1_500
DEEPGRAM_EOT_GRACE_SECONDS = 0.7
SMART_TURN_COMPLETE_THRESHOLD = 0.5
SMART_TURN_AUDIO_SECONDS = 8


@dataclass
class DeepgramEOTEventFrame(SystemFrame):
    transcript: str
    vad_state: str
    decision: str
    smart_turn_probability: float | None = None
    cancellation_reason: str | None = None
    deepgram_event: str = "speech_final"


@dataclass
class _PendingEOT:
    transcript: TranscriptionFrame
    direction: FrameDirection
    candidate_at: float
    vad_state: str
    task: asyncio.Task | None = None
    smart_turn_probability: float | None = None


class BoundedDeepgramSTTService(DeepgramSTTService):
    async def _disconnect(self):
        try:
            await asyncio.wait_for(
                super()._disconnect(),
                timeout=DEEPGRAM_DISCONNECT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("{}: forcing Deepgram disconnect after timeout", self)
            self._connection = None
            if self._connection_task:
                await self.cancel_task(self._connection_task)
                self._connection_task = None


class DeepgramEOTCoordinator(FrameProcessor):
    def __init__(self, smart_turn: BaseTurnAnalyzer | None = None):
        super().__init__()
        self._smart_turn = smart_turn or LocalSmartTurnAnalyzerV3()
        self._pending_eot: _PendingEOT | None = None
        self._vad_user_speaking = False
        self._audio_chunks: deque[tuple[bytes, bool]] = deque()
        self._audio_bytes = 0
        self._sample_rate = 16_000

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        self._sample_rate = setup.audio_in_sample_rate
        self._smart_turn.set_sample_rate(self._sample_rate)

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            self._append_audio(frame)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._vad_user_speaking = True
            self._smart_turn.update_vad_start_secs(frame.start_secs)
            if self._pending_eot:
                self._pending_eot.vad_state = "speaking"
            await self._cancel_pending_eot("vad_resumed")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._vad_user_speaking = False
        elif self._pending_eot and isinstance(
            frame, (InterimTranscriptionFrame, TranscriptionFrame)
        ):
            await self._cancel_pending_eot("new_transcript")

        if isinstance(frame, TranscriptionFrame) and getattr(
            frame.result, "speech_final", False
        ):
            await self._start_candidate(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def cleanup(self):
        await self._cancel_pending_eot("cleanup", emit_transcript=False)
        await self._smart_turn.cleanup()
        await super().cleanup()

    def _append_audio(self, frame: InputAudioRawFrame) -> None:
        self._sample_rate = frame.sample_rate
        self._audio_chunks.append((frame.audio, self._vad_user_speaking))
        self._audio_bytes += len(frame.audio)
        max_bytes = self._sample_rate * 2 * SMART_TURN_AUDIO_SECONDS
        while self._audio_bytes > max_bytes and self._audio_chunks:
            audio, _ = self._audio_chunks.popleft()
            self._audio_bytes -= len(audio)

    async def _start_candidate(
        self, transcript: TranscriptionFrame, direction: FrameDirection
    ) -> None:
        vad_state = "speaking" if self._vad_user_speaking else "quiet"
        pending = _PendingEOT(
            transcript=transcript,
            direction=direction,
            candidate_at=asyncio.get_running_loop().time(),
            vad_state=vad_state,
        )
        self._pending_eot = pending
        await self._emit_eot_event(pending, decision="candidate")

        if self._vad_user_speaking:
            await self._cancel_pending_eot("vad_speaking_at_candidate")
            return

        pending.task = asyncio.create_task(self._evaluate_candidate(pending))

    async def _evaluate_candidate(self, pending: _PendingEOT) -> None:
        state, metrics = await self._analyze_smart_turn()
        if self._pending_eot is not pending:
            return

        probability = getattr(metrics, "probability", None)
        pending.smart_turn_probability = probability
        if (
            state != EndOfTurnState.COMPLETE
            or probability is None
            or probability <= SMART_TURN_COMPLETE_THRESHOLD
        ):
            await self._cancel_pending_eot("smart_turn_incomplete")
            return

        grace_remaining = (
            pending.candidate_at
            + DEEPGRAM_EOT_GRACE_SECONDS
            - asyncio.get_running_loop().time()
        )
        if grace_remaining > 0:
            await asyncio.sleep(grace_remaining)
        if self._pending_eot is pending:
            await self._commit_eot(pending)

    async def _analyze_smart_turn(self):
        audio_snapshot = list(self._audio_chunks)
        self._smart_turn.clear()
        for audio, is_speech in audio_snapshot:
            self._smart_turn.append_audio(audio, is_speech)
        return await self._smart_turn.analyze_end_of_turn()

    async def _commit_eot(self, pending: _PendingEOT) -> None:
        self._pending_eot = None
        pending.transcript.finalized = True
        await self._emit_eot_event(pending, decision="committed")
        await self.push_frame(UserStoppedSpeakingFrame(), pending.direction)
        await self.push_frame(pending.transcript, pending.direction)
        self._audio_chunks.clear()
        self._audio_bytes = 0
        self._smart_turn.clear()

    async def _cancel_pending_eot(
        self, reason: str, *, emit_transcript: bool = True
    ) -> None:
        pending = self._pending_eot
        if pending is None:
            return

        self._pending_eot = None
        task = pending.task
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        pending.transcript.finalized = False
        await self._emit_eot_event(
            pending,
            decision="cancelled",
            cancellation_reason=reason,
        )
        if emit_transcript:
            await self.push_frame(pending.transcript, pending.direction)

    async def _emit_eot_event(
        self,
        pending: _PendingEOT,
        *,
        decision: str,
        cancellation_reason: str | None = None,
    ) -> None:
        logger.info(
            "Deepgram EOT {} | text={} vad={} smart_turn_probability={} reason={}",
            decision,
            pending.transcript.text,
            pending.vad_state,
            pending.smart_turn_probability,
            cancellation_reason,
        )
        await self.push_frame(
            DeepgramEOTEventFrame(
                transcript=pending.transcript.text,
                vad_state=pending.vad_state,
                decision=decision,
                smart_turn_probability=pending.smart_turn_probability,
                cancellation_reason=cancellation_reason,
            ),
            pending.direction,
        )


class DeepgramEndpointingStopStrategy(BaseUserTurnStopStrategy):
    async def process_frame(self, frame) -> ProcessFrameResult:
        if isinstance(frame, TranscriptionFrame) and frame.finalized:
            await self.trigger_user_turn_stopped(enable_user_speaking_frames=False)
        return ProcessFrameResult.CONTINUE


def create_deepgram_stt(api_key: str | None = None) -> DeepgramSTTService:
    api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY is required")

    settings = DeepgramSTTService.Settings(
        model=DEEPGRAM_MODEL,
        language="multi",
        endpointing=DEEPGRAM_ENDPOINTING_MS,
        interim_results=True,
        numerals=True,
        punctuate=True,
        smart_format=True,
    )
    return BoundedDeepgramSTTService(api_key=api_key, settings=settings)
