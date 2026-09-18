import os
from pathlib import Path

from loguru import logger
from pipecat.audio.utils import pcm_to_wav
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from .handshake import CallMeta

RECORDINGS_DIR_VAR = "CALL_RECORDINGS_DIR"


def create_recorder(meta: CallMeta) -> AudioBufferProcessor | None:
    """Record the call as a WAV with both voices on one track, in real time.

    Returns None unless CALL_RECORDINGS_DIR is set, so recording is opt-in.
    The buffer flushes itself on EndFrame and CancelFrame, which is every way
    a call ends here.
    """
    directory = os.getenv(RECORDINGS_DIR_VAR, "").strip()
    if not directory:
        return None

    path = Path(directory) / f"{meta.call_id}.wav"
    recorder = AudioBufferProcessor(auto_start_recording=True)

    @recorder.event_handler("on_audio_data")
    async def _on_audio_data(
        _recorder, audio: bytes, sample_rate: int, num_channels: int
    ):
        # Only what the transport actually sent: audio dropped by a barge-in is
        # absent, because that is what the caller heard.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pcm_to_wav(audio, sample_rate, num_channels))
        logger.info(
            "call recorded | call_id={} path={} seconds={:.1f}",
            meta.call_id,
            path,
            len(audio) / (sample_rate * 2 * num_channels),
        )

    return recorder
