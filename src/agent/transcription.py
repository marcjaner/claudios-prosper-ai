from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
    LocalSmartTurnAnalyzerV3,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from stt import create_deepgram_stt
from tts import create_tts
from twilio import AgentFactory, CallMeta

from .hardcoded_reply import HardcodedReply

CompletedTurnCallback = Callable[[CallMeta, str], Awaitable[None]]


async def log_completed_turn(meta: CallMeta, content: str) -> None:
    logger.info("completed user turn | call_id={} text={}", meta.call_id, content)


def create_user_aggregator(meta: CallMeta, on_completed_turn: CompletedTurnCallback):
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
    aggregator = LLMContextAggregatorPair(
        LLMContext(), user_params=params, realtime_service_mode=False
    ).user()

    @aggregator.event_handler("on_user_turn_stopped")
    async def _on_user_turn_stopped(_aggregator, _strategy, message):
        if message.content:
            await on_completed_turn(meta, message.content)

    return aggregator


def create_transcription_agent(
    on_completed_turn: CompletedTurnCallback = log_completed_turn,
    reply: str | None = None,
) -> AgentFactory:
    async def build_agent(meta: CallMeta):
        processors = [
            create_deepgram_stt(),
            create_user_aggregator(meta, on_completed_turn),
        ]
        if reply:
            # Until the LLM exists: the same spoken answer to every turn.
            processors += [HardcodedReply(reply), create_tts()]
        return processors

    return build_agent
