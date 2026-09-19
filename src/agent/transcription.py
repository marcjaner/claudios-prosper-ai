import logging
from collections.abc import Awaitable, Callable

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, InterimTranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from observability import emit, update_call
from storage import CallRepository, Database
from stt import (
    DeepgramEndpointingStopStrategy,
    DeepgramEOTCoordinator,
    create_deepgram_stt,
)
from tts import create_tts
from twilio import AgentFactory, CallMeta

from .reply import AgentReply

logger = logging.getLogger(__name__)

CompletedTurnCallback = Callable[[CallMeta, str], Awaitable[None]]
USER_TURN_STOP_TIMEOUT_SECONDS = 6
INITIAL_GREETING = "Clínica Arenal, ¿en qué puedo ayudarle?"


async def log_completed_turn(meta: CallMeta, content: str) -> None:
    logger.info("completed user turn | call_id=%s text=%s", meta.call_id, content)


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


def create_user_aggregator(meta: CallMeta, on_completed_turn: CompletedTurnCallback):
    params = LLMUserAggregatorParams(
        user_turn_stop_timeout=USER_TURN_STOP_TIMEOUT_SECONDS,
        user_turn_strategies=UserTurnStrategies(
            stop=[DeepgramEndpointingStopStrategy()]
        ),
    )
    aggregator = LLMContextAggregatorPair(
        LLMContext(), user_params=params, realtime_service_mode=False
    ).user()

    @aggregator.event_handler("on_user_turn_stopped")
    async def _on_user_turn_stopped(_aggregator, _strategy, message):
        logger.info(
            "user turn stopped | call_id=%s content=%r", meta.call_id, message.content
        )
        if message.content:
            emit(meta.call_id, "stt_final", {"text": message.content})
            update_call(meta.call_id, state="thinking")
            await on_completed_turn(meta, message.content)

    return aggregator


def create_transcription_agent(
    on_completed_turn: CompletedTurnCallback = log_completed_turn,
) -> AgentFactory:
    async def build_agent(meta: CallMeta):
        logger.info("building call pipeline | call_id=%s", meta.call_id)
        database = Database()
        await database.init()
        repository = CallRepository(database)
        await repository.seed_default_guardrails()
        await repository.create_call(meta.call_id, meta.from_number, meta.connected_at)
        processors = [
            VADProcessor(vad_analyzer=SileroVADAnalyzer()),
            create_deepgram_stt(),
            DeepgramEOTCoordinator(),
            TranscriptObserver(meta),
            create_user_aggregator(meta, on_completed_turn),
            AgentReply(meta.call_id, repository),
            create_tts(),
        ]
        logger.info(
            "call pipeline ready | call_id=%s processors=%s",
            meta.call_id,
            len(processors),
        )
        return processors

    return build_agent
