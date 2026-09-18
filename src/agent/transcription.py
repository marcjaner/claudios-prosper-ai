from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from stt import create_deepgram_stt
from twilio import AgentFactory, CallMeta

USER_SPEECH_TIMEOUT_SECONDS = 0.65

CompletedTurnCallback = Callable[[CallMeta, str], Awaitable[None]]


async def log_completed_turn(meta: CallMeta, content: str) -> None:
    logger.info("completed user turn | call_id={} text={}", meta.call_id, content)


def create_user_aggregator(meta: CallMeta, on_completed_turn: CompletedTurnCallback):
    params = LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),
        user_turn_strategies=UserTurnStrategies(
            stop=[
                SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=USER_SPEECH_TIMEOUT_SECONDS
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
) -> AgentFactory:
    async def build_agent(meta: CallMeta):
        return [
            create_deepgram_stt(),
            create_user_aggregator(meta, on_completed_turn),
        ]

    return build_agent
