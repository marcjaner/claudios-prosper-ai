import asyncio
import json

from pipecat.frames.frames import EndFrame, InputAudioRawFrame

from twilio.serializer import create_serializer


def test_stop_event_ends_the_pipeline():
    serializer = create_serializer("MZ123", "CA456")

    frame = asyncio.run(serializer.deserialize(json.dumps({"event": "stop"})))

    assert isinstance(frame, EndFrame)


def test_input_audio_is_not_echoed_to_the_caller():
    serializer = create_serializer("MZ123", "CA456")
    frame = InputAudioRawFrame(audio=b"\x00\x00", sample_rate=16_000, num_channels=1)

    serialized = asyncio.run(serializer.serialize(frame))

    assert serialized is None
