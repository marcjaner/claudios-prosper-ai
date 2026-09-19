import json

from pipecat.frames.frames import EndFrame, Frame, InputAudioRawFrame
from pipecat.serializers.twilio import TwilioFrameSerializer


class StopAwareTwilioFrameSerializer(TwilioFrameSerializer):
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, InputAudioRawFrame):
            return None
        return await super().serialize(frame)

    async def deserialize(self, data: str | bytes):
        if json.loads(data).get("event") == "stop":
            return EndFrame()
        return await super().deserialize(data)


def create_serializer(stream_sid: str, call_sid: str) -> TwilioFrameSerializer:
    # auto_hang_up defaults on and raises without Twilio REST credentials.
    # The harness has none and ends a call by closing the socket.
    return StopAwareTwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )
