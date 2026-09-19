import logging
import time

from pipecat.frames.frames import Frame, LLMContextFrame, SystemFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from observability.frames import TTSRequestedFrame

from .agent import run_agent_turn

logger = logging.getLogger(__name__)
CALL_LIMIT_SECONDS = 180


class AgentReply(FrameProcessor):
    def __init__(self, call_id, repository):
        super().__init__()
        self.call_id = call_id
        self.repository = repository
        self.started_at = time.monotonic()

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
        try:
            async for response in run_agent_turn(
                content,
                self.call_id,
                self.repository,
                event_sink=self._emit_event,
                seconds_remaining=max(
                    0, CALL_LIMIT_SECONDS - (time.monotonic() - self.started_at)
                ),
            ):
                logger.info(
                    "agent response ready | call_id=%s answer=%r tool_calls=%s",
                    self.call_id,
                    response.immediate_answer,
                    len(response.tool_calls),
                )
                await self.push_frame(
                    TTSRequestedFrame(response.immediate_answer),
                    FrameDirection.DOWNSTREAM,
                )
                await self.push_frame(
                    TTSSpeakFrame(response.immediate_answer),
                    FrameDirection.DOWNSTREAM,
                )
        except Exception:
            logger.exception("agent turn failed | call_id=%s", self.call_id)

    async def _emit_event(self, frame: SystemFrame) -> None:
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)
