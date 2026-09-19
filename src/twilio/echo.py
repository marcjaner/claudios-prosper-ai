from collections.abc import Sequence

from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .handshake import CallMeta


class EchoProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, InputAudioRawFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                ),
                direction,
            )
            return
        await self.push_frame(frame, direction)


async def build_echo_agent(meta: CallMeta) -> Sequence[FrameProcessor]:
    # Closes the loop at the transport boundary so the wire can be tested
    # without STT or TTS.
    return [EchoProcessor()]
