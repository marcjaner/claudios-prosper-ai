from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
    LocalSmartTurnAnalyzerV3,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, InterimTranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from observability import emit, update_call
from stt import create_deepgram_stt
from tts import create_tts
from twilio import AgentFactory, CallMeta

from .clinic_api import ClinicApi
from .reply import AgentReply

CompletedTurnCallback = Callable[[CallMeta, str], Awaitable[None]]


async def log_completed_turn(meta: CallMeta, content: str) -> None:
    logger.info("completed user turn | call_id={} text={}", meta.call_id, content)


class TranscriptObserver(FrameProcessor):
    def __init__(self, meta: CallMeta):
        super().__init__()
        self._meta = meta

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterimTranscriptionFrame):
            emit(self._meta.call_id, "stt_partial", {"text": frame.text})
            update_call(self._meta.call_id, state="listening")
        await self.push_frame(frame, direction)


def create_context_aggregators(
    meta: CallMeta, on_completed_turn: CompletedTurnCallback
) -> LLMContextAggregatorPair:
    params = LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),
        user_turn_strategies=UserTurnStrategies(
            stop=[
                TurnAnalyzerUserTurnStopStrategy(
                    turn_analyzer=LocalSmartTurnAnalyzerV3()
                )
            ]
        ),
    )
    aggregators = LLMContextAggregatorPair(
        LLMContext(), user_params=params, realtime_service_mode=False
    )
    user_aggregator = aggregators.user()

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def _on_user_turn_stopped(_aggregator, _strategy, message):
        if message.content:
            emit(meta.call_id, "stt_final", {"text": message.content})
            update_call(meta.call_id, state="thinking")
            await on_completed_turn(meta, message.content)

    return aggregators


def create_user_aggregator(meta: CallMeta, on_completed_turn: CompletedTurnCallback):
    return create_context_aggregators(meta, on_completed_turn).user()


def create_transcription_agent(
    on_completed_turn: CompletedTurnCallback = log_completed_turn,
) -> AgentFactory:
    async def build_agent(meta: CallMeta):
        aggregators = create_context_aggregators(meta, on_completed_turn)
        return [
            create_deepgram_stt(),
            TranscriptObserver(meta),
            aggregators.user(),
            AgentReply(meta, ClinicApi.from_environment()),
            create_tts(),
            aggregators.assistant(),
        ]

    return build_agent
