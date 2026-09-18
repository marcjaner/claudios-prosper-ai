from pipecat.frames.frames import Frame, LLMContextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class HardcodedReply(FrameProcessor):
    """Stands in for the LLM: answers every completed caller turn with the same text."""

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        # The turn detector emits this when the caller stops; an LLM would consume it.
        if isinstance(frame, LLMContextFrame) and not frame.speculation:
            await self.push_frame(TTSSpeakFrame(self._text), direction)
            return
        await self.push_frame(frame, direction)
