import pytest

from tts import create_tts


def test_default_provider_is_deepgram():
    from pipecat.services.deepgram.tts import DeepgramTTSService

    tts = create_tts({"DEEPGRAM_API_KEY": "k"})
    assert isinstance(tts, DeepgramTTSService)


def test_deepgram_selected_by_env():
    from pipecat.services.deepgram.tts import DeepgramTTSService

    tts = create_tts({"TTS_PROVIDER": "deepgram", "DEEPGRAM_API_KEY": "k"})
    assert isinstance(tts, DeepgramTTSService)


def test_provider_name_is_case_insensitive():
    from pipecat.services.deepgram.tts import DeepgramTTSService

    tts = create_tts({"TTS_PROVIDER": " Deepgram ", "DEEPGRAM_API_KEY": "k"})
    assert isinstance(tts, DeepgramTTSService)


def test_unknown_provider_names_the_value():
    with pytest.raises(ValueError, match="nope"):
        create_tts({"TTS_PROVIDER": "nope"})


def test_missing_key_names_the_variable():
    with pytest.raises(ValueError, match="CARTESIA_API_KEY"):
        create_tts({"TTS_PROVIDER": "cartesia", "TTS_VOICE": "v"})

    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        create_tts({"TTS_PROVIDER": "deepgram"})


def test_cartesia_requires_a_voice():
    with pytest.raises(ValueError, match="TTS_VOICE"):
        create_tts({"TTS_PROVIDER": "cartesia", "CARTESIA_API_KEY": "k"})
