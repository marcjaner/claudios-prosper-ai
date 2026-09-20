"""Let a person take a live call over from the agent.

Two processors share one bridge. `OperatorBridge` sits right after transport
input, where the caller's audio still exists: once an operator holds the call
it diverts that audio to them instead of STT, and pushes their microphone out
to the caller. `AgentMute` sits after the agent and drops any speech a late
transcription still manages to produce.
"""

import asyncio

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Both directions cross the operator socket as PCM16 mono at this rate; the
# output transport resamples on its way to the wire.
OPERATOR_SAMPLE_RATE = 16_000
# The hand-off phrase should have played out by then; if the TTS never
# reports back, the operator still gets the line.
HANDOFF_TIMEOUT_SECONDS = 6

MUTED_WHILE_HELD = (TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame)


class OperatorBridge(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.active = False
        # Caller audio for the operator; None means the call is over.
        self.caller_audio: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._bot_stopped_speaking = asyncio.Event()

    async def take_over(self, *, after_handoff: bool = False) -> None:
        if after_handoff:
            # The output transport reports upstream once the caller has heard
            # the whole phrase, not when the TTS finished generating it.
            self._bot_stopped_speaking.clear()
            try:
                await asyncio.wait_for(
                    self._bot_stopped_speaking.wait(), HANDOFF_TIMEOUT_SECONDS
                )
            except TimeoutError:
                pass
        self.active = True
        # Cuts whatever the agent is saying or thinking and clears Twilio's buffer.
        await self.broadcast_interruption()

    async def speak(self, pcm: bytes) -> None:
        await self.push_frame(
            OutputAudioRawFrame(
                audio=pcm, sample_rate=OPERATOR_SAMPLE_RATE, num_channels=1
            ),
            FrameDirection.DOWNSTREAM,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_stopped_speaking.set()
        if self.active and isinstance(frame, InputAudioRawFrame):
            self.caller_audio.put_nowait(frame.audio)
            return
        if self.active and isinstance(frame, (EndFrame, CancelFrame)):
            self.caller_audio.put_nowait(None)
        await self.push_frame(frame, direction)


class AgentMute(FrameProcessor):
    def __init__(self, bridge: OperatorBridge):
        super().__init__()
        self._bridge = bridge

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if self._bridge.active and isinstance(frame, MUTED_WHILE_HELD):
            return
        await self.push_frame(frame, direction)
