import os

from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transcriptions.language import Language

DEEPGRAM_MODEL = "nova-3-general"


def create_deepgram_stt(api_key: str | None = None) -> DeepgramSTTService:
    api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY is required")

    settings = DeepgramSTTService.Settings(
        model=DEEPGRAM_MODEL,
        language=Language.ES,
        interim_results=True,
        numerals=True,
        punctuate=True,
        smart_format=True,
    )
    return DeepgramSTTService(api_key=api_key, settings=settings)
