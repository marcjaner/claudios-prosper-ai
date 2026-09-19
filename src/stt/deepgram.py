import asyncio
import os

from loguru import logger
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.base_user_turn_stop_strategy import (
    BaseUserTurnStopStrategy,
)

DEEPGRAM_DISCONNECT_TIMEOUT_SECONDS = 2
DEEPGRAM_MODEL = "nova-3-general"
DEEPGRAM_ENDPOINTING_MS = 1_500
DEEPGRAM_EOT_GRACE_SECONDS = 0.7


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


class DeepgramEndpointingProcessor(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._pending_eot: tuple[TranscriptionFrame, FrameDirection] | None = None
        self._pending_eot_task: asyncio.Task | None = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self._pending_eot and isinstance(
            frame, (InterimTranscriptionFrame, TranscriptionFrame)
        ):
            await self._cancel_pending_eot()

        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and getattr(frame.result, "speech_final", False)
        ):
            frame.finalized = True
            self._pending_eot = (frame, direction)
            self._pending_eot_task = asyncio.create_task(self._commit_eot())
            return

        await self.push_frame(frame, direction)

    async def cleanup(self):
        await self._cancel_pending_eot()
        await super().cleanup()

    async def _commit_eot(self):
        try:
            await asyncio.sleep(DEEPGRAM_EOT_GRACE_SECONDS)
        except asyncio.CancelledError:
            return

        pending_eot = self._pending_eot
        self._pending_eot = None
        self._pending_eot_task = None
        if pending_eot is None:
            return

        frame, direction = pending_eot
        await self.push_frame(UserStoppedSpeakingFrame(), direction)
        await self.push_frame(frame, direction)

    async def _cancel_pending_eot(self):
        task = self._pending_eot_task
        self._pending_eot_task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        pending_eot = self._pending_eot
        self._pending_eot = None
        if pending_eot is None:
            return

        frame, direction = pending_eot
        frame.finalized = False
        await self.push_frame(frame, direction)


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
