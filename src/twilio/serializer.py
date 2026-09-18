from pipecat.serializers.twilio import TwilioFrameSerializer


def create_serializer(stream_sid: str, call_sid: str) -> TwilioFrameSerializer:
    # auto_hang_up defaults on and raises without Twilio REST credentials.
    # The harness has none and ends a call by closing the socket.
    return TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )
