import asyncio
import logging
from collections.abc import Awaitable, Callable

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from observability import emit, update_call
from observability.frames import TTSRequestedFrame
from storage import CallRepository, Database
from stt import (
    DeepgramEndpointingStopStrategy,
    DeepgramEOTCoordinator,
    create_deepgram_stt,
)
from tts import create_tts
from twilio import AgentFactory, CallMeta

from .language import DEFAULT_LANGUAGE, CallLanguage, LanguageTracker, phrases
from .reply import AgentReply
from .stage_runtime import CallGraph

logger = logging.getLogger(__name__)

CompletedTurnCallback = Callable[[CallMeta, str], Awaitable[None]]
USER_TURN_STOP_TIMEOUT_SECONDS = 6
EMPTY_TURN_RECOVERY_MESSAGE = "Sorry, I didn't catch that. Could you repeat it?"
# The greeting plays before the caller has said a word, so it is always the default.
INITIAL_GREETING = phrases(DEFAULT_LANGUAGE).greeting


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
            return
        logger.warning("empty user turn | call_id=%s", meta.call_id)
        await _aggregator.push_frame(
            TTSRequestedFrame(EMPTY_TURN_RECOVERY_MESSAGE), FrameDirection.DOWNSTREAM
        )
        await _aggregator.push_frame(
            TTSSpeakFrame(EMPTY_TURN_RECOVERY_MESSAGE), FrameDirection.DOWNSTREAM
        )

    return aggregator


def create_transcription_agent(
    on_completed_turn: CompletedTurnCallback = log_completed_turn,
    database: Database | None = None,
) -> AgentFactory:
    database = database or Database()
    repository = CallRepository(database)
    database_init_lock = asyncio.Lock()
    is_database_initialized = False

    async def build_agent(meta: CallMeta):
        nonlocal is_database_initialized
        logger.info("building call pipeline | call_id=%s", meta.call_id)
        if not is_database_initialized:
            async with database_init_lock:
                if not is_database_initialized:
                    await database.init()
                    is_database_initialized = True
        await repository.seed_default_guardrails()
        await repository.create_call(meta.call_id, meta.from_number, meta.connected_at)
        state = CallGraph.start(call_id=meta.call_id)
        language = CallLanguage()
        emit(
            meta.call_id,
            "stage_entered",
            {
                "turn": 0,
                "step": 0,
                "stage": state.stage_id,
                "from": None,
                "cleared": [],
            },
        )
        processors = [
            VADProcessor(vad_analyzer=SileroVADAnalyzer()),
            create_deepgram_stt(),
            DeepgramEOTCoordinator(),
            TranscriptObserver(meta),
            LanguageTracker(meta.call_id, language),
            create_user_aggregator(meta, on_completed_turn),
            AgentReply(meta.call_id, repository, state, language),
            create_tts(),
        ]
        logger.info(
            "call pipeline ready | call_id=%s processors=%s",
            meta.call_id,
            len(processors),
        )
        return processors

    return build_agent
