import asyncio
import os

from loguru import logger
from pipecat.services.deepgram.stt import DeepgramSTTService

DEEPGRAM_DISCONNECT_TIMEOUT_SECONDS = 2
DEEPGRAM_MODEL = "nova-3-general"


class BoundedDeepgramSTTService(DeepgramSTTService):
    async def _disconnect(self):
        try:
            await asyncio.wait_for(
                super()._disconnect(),
                timeout=DEEPGRAM_DISCONNECT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("{}: forcing Deepgram disconnect after timeout", self)
            self._connection = None
            if self._connection_task:
                await self.cancel_task(self._connection_task)
                self._connection_task = None


def create_deepgram_stt(api_key: str | None = None) -> DeepgramSTTService:
    api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY is required")

    settings = DeepgramSTTService.Settings(
        model=DEEPGRAM_MODEL,
        language="multi",
        interim_results=True,
        numerals=True,
        punctuate=True,
        smart_format=True,
    )
    return BoundedDeepgramSTTService(api_key=api_key, settings=settings)
