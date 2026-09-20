import pytest
from pipecat.services.settings import TTSSettings
from pipecat.transcriptions.language import Language

from agent.language import DEFAULT_LANGUAGE, CallLanguage, phrases, reply_instruction
from tts import language_settings


def test_a_call_starts_in_english():
    assert CallLanguage().language == DEFAULT_LANGUAGE == Language.EN


@pytest.mark.parametrize(
    "text",
    [
        "Jorge Martínez",
        "Montserrat González Martín",
        "11 March 1940",
        "V4656266J",
        "Yes.",
        "Okay.",
        "María de la Cruz",
        "My name is Jorge Martínez and I need an appointment.",
        "I need a doctor who speaks Catalan, please.",
        "I don't speak Spanish. Please speak English.",
        "I don't speak Catalan",
        "Please do not speak Spanish",
    ],
)
def test_names_identifiers_and_provider_languages_do_not_switch_english(text):
    call = CallLanguage()
    assert not call.observe_turn(text)
    assert call.language == Language.EN


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hola, quiero una cita con medicina general.", Language.ES),
        ("Hola.", Language.ES),
        ("Perdone, no la acabo de entender. La Clínica Arenal.", Language.ES),
        ("Bon dia, voldria demanar hora amb un metge.", Language.CA),
        ("Can we speak in Spanish please?", Language.ES),
        ("Can we speak Spanish? I need a doctor.", Language.ES),
        ("Podemos hablar en inglés?", Language.EN),
        ("Could we switch to Catalan?", Language.CA),
    ],
)
def test_complete_turn_follows_substantive_speech_or_explicit_request(text, expected):
    call = CallLanguage()
    call.observe_turn(text)
    assert call.language == expected


def test_short_answers_preserve_the_selected_language():
    call = CallLanguage()
    call.observe_turn("Hola, quiero una cita con medicina general.")
    for text in (
        "Sí.",
        "Vale.",
        "Jorge Martínez",
        "13 diciembre 1995",
        "Sí, el martes.",
        "Sí, la de la tarde.",
        "El día 23.",
    ):
        call.observe_turn(text)
        assert call.language == Language.ES
    assert call.observe_turn("Actually, can we speak English please?")
    assert call.language == Language.EN


def test_catalan_recovery_is_localized():
    assert phrases(Language.CA).name == "Catalan"
    assert phrases(Language.CA).no_answer != phrases(Language.EN).no_answer
    assert reply_instruction(Language.CA) == "Reply to the caller in Catalan."


def test_unwritten_languages_keep_the_clinics_filler_but_their_own_name():
    assert phrases(Language.FR) is phrases(DEFAULT_LANGUAGE)
    assert reply_instruction(Language.FR) == "Reply to the caller in fr."


def test_deepgram_follows_a_language_by_swapping_the_voice(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "deepgram")
    assert language_settings(Language.EN) == TTSSettings(voice="aura-2-arcas-en")
    assert language_settings(Language.FR) is None


def test_catalan_does_not_send_an_unsupported_cartesia_language(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "cartesia")
    assert language_settings(Language.CA) == TTSSettings(language=Language.ES)
