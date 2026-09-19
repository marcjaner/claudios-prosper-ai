import asyncio
from collections.abc import Callable, Iterator

from loguru import logger
from pipecat.frames.frames import Frame, LLMContextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from twilio import CallMeta

from .agent import run_agent
from .clinic_api import ClinicApi
from .llm import LLMClient
from .models import ToolResult

AGENT_ERROR_REPLY = "I'm sorry, I couldn't process that. Could you try again?"
AgentRunner = Callable[..., Iterator[str | ToolResult]]


class AgentReply(FrameProcessor):
    def __init__(
        self,
        meta: CallMeta,
        clinic_api: ClinicApi,
        *,
        client: LLMClient | None = None,
        runner: AgentRunner = run_agent,
    ):
        super().__init__()
        self._meta = meta
        self._clinic_api = clinic_api
        self._client = client
        self._runner = runner

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame) or frame.speculation:
            await self.push_frame(frame, direction)
            return

        prompt = self._conversation_prompt(frame)
        try:
            events = await asyncio.to_thread(
                lambda: list(
                    self._runner(
                        prompt,
                        self._client,
                        clinic_api=self._clinic_api,
                        call_id=self._meta.call_id,
                    )
                )
            )
            answer = next(event for event in events if isinstance(event, str))
        except Exception:  # noqa: BLE001 - one failed turn must not end the call
            logger.exception("agent turn failed | call_id={}", self._meta.call_id)
            answer = AGENT_ERROR_REPLY

        await self.push_frame(TTSSpeakFrame(answer), FrameDirection.DOWNSTREAM)

    async def cleanup(self):
        self._clinic_api.close()
        await super().cleanup()

    def _conversation_prompt(self, frame: LLMContextFrame) -> str:
        lines = [
            f"Call connected at {self._meta.connected_at.isoformat()}.",
            f"Caller ID hint: {self._meta.from_number or 'withheld'}.",
            "Conversation:",
        ]
        for message in frame.context.messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            text = self._message_text(message.get("content"))
            if role and text:
                lines.append(f"{role}: {text}")
        return "\n".join(lines)

    @staticmethod
    def _message_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        return ""
