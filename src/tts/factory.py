"""Build the TTS service for the voice pipeline from environment variables.

TTS_PROVIDER picks the provider ("cartesia" or "deepgram"). Each provider reads
its own API key. TTS_VOICE and TTS_LANGUAGE override the defaults.
"""

import os
from collections.abc import Mapping

from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language

PROVIDERS = ("cartesia", "deepgram")
DEFAULT_PROVIDER = "cartesia"
DEFAULT_LANGUAGE = "es"
CARTESIA_MODEL = "sonic-3.6"
DEEPGRAM_VOICE = "aura-2-nestor-es"


def create_tts(env: Mapping[str, str] | None = None) -> TTSService:
    """Return a configured pipecat TTS service. Reads os.environ unless env is given."""
    env = os.environ if env is None else env
    provider = env.get("TTS_PROVIDER", DEFAULT_PROVIDER).strip().lower()

    if provider == "cartesia":
        return _create_cartesia(env)
    if provider == "deepgram":
        return _create_deepgram(env)
    raise ValueError(f"Unknown TTS_PROVIDER={provider!r}. Expected one of {PROVIDERS}.")


def _create_cartesia(env: Mapping[str, str]) -> TTSService:
    from pipecat.services.cartesia.tts import CartesiaTTSService

    return CartesiaTTSService(
        api_key=_require(env, "CARTESIA_API_KEY"),
        settings=CartesiaTTSService.Settings(
            model=env.get("TTS_MODEL", CARTESIA_MODEL),
            voice=_require(env, "TTS_VOICE"),
            language=Language(env.get("TTS_LANGUAGE", DEFAULT_LANGUAGE)),
        ),
    )


def _create_deepgram(env: Mapping[str, str]) -> TTSService:
    from pipecat.services.deepgram.tts import DeepgramTTSService

    # Deepgram encodes the language in the voice name (aura-2-<name>-<lang>).
    return DeepgramTTSService(
        api_key=_require(env, "DEEPGRAM_API_KEY"),
        settings=DeepgramTTSService.Settings(
            voice=env.get("TTS_VOICE", DEEPGRAM_VOICE)
        ),
    )


def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} is required. Add it to .env (see .env.example).")
    return value
