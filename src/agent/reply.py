import logging

from pipecat.frames.frames import Frame, LLMContextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .agent import run_agent_turn

logger = logging.getLogger(__name__)


class AgentReply(FrameProcessor):
    def __init__(self, call_id, repository):
        super().__init__()
        self.call_id = call_id
        self.repository = repository

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and not frame.speculation:
            messages = getattr(frame.context, "messages", [])
            content = messages[-1].get("content", "") if messages else ""
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            logger.info("agent turn received | call_id=%s prompt=%r", self.call_id, content)
            try:
                async for response in run_agent_turn(content, self.call_id, self.repository):
                    logger.info("agent response ready | call_id=%s answer=%r tool_calls=%s", self.call_id, response.immediate_answer, len(response.tool_calls))
                    await self.push_frame(TTSSpeakFrame(response.immediate_answer), FrameDirection.DOWNSTREAM)
            except Exception:
                logger.exception("agent turn failed | call_id=%s", self.call_id)
            return
        await self.push_frame(frame, direction)
