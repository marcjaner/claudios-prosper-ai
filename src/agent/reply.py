import logging

from pipecat.frames.frames import Frame, LLMContextFrame, SystemFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from observability import emit, update_call
from observability.frames import TTSRequestedFrame

from .agent import run_agent_turn
from .language import CallLanguage, phrases
from .stage_runtime import CallGraph

logger = logging.getLogger(__name__)


class AgentReply(FrameProcessor):
    def __init__(
        self,
        call_id,
        repository,
        state: CallGraph | None = None,
        language: CallLanguage | None = None,
    ):
        super().__init__()
        self.call_id = call_id
        self.repository = repository
        # Frozen for the call: saving in the builder must not move live ground.
        self.state = state or CallGraph.start(call_id=call_id)
        self.language = language or CallLanguage()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame) or frame.speculation:
            await self.push_frame(frame, direction)
            return

        messages = getattr(frame.context, "messages", [])
        content = messages[-1].get("content", "") if messages else ""
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        logger.info("agent turn received | call_id=%s prompt=%r", self.call_id, content)
        update_call(self.call_id, state="thinking")
        # Read once: the turn answers in the language the caller was speaking
        # when it started, even if they interrupt it in another.
        language = self.language.language
        try:
            async for response in run_agent_turn(
                content,
                self.call_id,
                self.repository,
                event_sink=self._emit_event,
                state=self.state,
                language=language,
            ):
                logger.info(
                    "agent response ready | call_id=%s answer=%r tool_calls=%s",
                    self.call_id,
                    response.immediate_answer,
                    len(response.tool_calls),
                )
                await self._speak(response.immediate_answer)
        except Exception as error:
            logger.exception("agent turn failed | call_id=%s", self.call_id)
            emit(self.call_id, "error", {"message": str(error)})
            await self._speak(phrases(language).error)

    async def _speak(self, text: str) -> None:
        emit(self.call_id, "tts", {"text": text})
        update_call(self.call_id, state="speaking")
        await self.push_frame(TTSRequestedFrame(text), FrameDirection.DOWNSTREAM)
        await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)

    async def _emit_event(self, frame: SystemFrame) -> None:
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)
