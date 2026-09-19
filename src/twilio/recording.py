import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pipecat.audio.utils import mix_audio, pcm_to_wav
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EagerTranscriptionFrame,
    ErrorFrame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMTextFrame,
    StartFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameDirection

from observability.frames import (
    LLMRequestFailedFrame,
    LLMRequestStartedFrame,
    LLMResponseFinishedFrame,
    ToolCallFinishedFrame,
    ToolCallStartedFrame,
    TTSRequestedFrame,
)

from .handshake import CallMeta

RECORDINGS_DIR_VAR = "CALL_RECORDINGS_DIR"
SCHEMA_VERSION = 1


class CallTimelineObserver(BaseObserver):
    def __init__(self, path: Path, started_at_ns: int):
        super().__init__()
        self._path = path
        self._pipeline_origin_ns: int | None = None
        self._pipeline_started_at_ns = started_at_ns
        self._recorded_frame_ids: set[int] = set()

    async def on_push_frame(self, data: FramePushed):
        if isinstance(data.frame, StartFrame) and self._pipeline_origin_ns is None:
            self._pipeline_origin_ns = data.timestamp
            self._pipeline_started_at_ns = time.monotonic_ns()
            return

        event = self._event_for_frame(data.frame, data.direction)
        if event is None or data.frame.id in self._recorded_frame_ids:
            return

        self._recorded_frame_ids.add(data.frame.id)
        self.write(
            event.pop("event"),
            elapsed_ms=self._frame_elapsed_ms(data.timestamp),
            source=data.source.name,
            direction=data.direction.name.lower(),
            **event,
        )

    def write(self, event: str, elapsed_ms: float | None = None, **details: Any):
        elapsed_ms = elapsed_ms if elapsed_ms is not None else self.elapsed_ms()
        record = {
            "at": datetime.now().astimezone().isoformat(),
            "elapsed_ms": round(elapsed_ms, 1),
            "event": event,
            **details,
        }
        with self._path.open("a", encoding="utf-8") as timeline:
            timeline.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    def elapsed_ms(self) -> float:
        return (time.monotonic_ns() - self._pipeline_started_at_ns) / 1_000_000

    def _frame_elapsed_ms(self, timestamp: int) -> float:
        origin = self._pipeline_origin_ns or 0
        return (timestamp - origin) / 1_000_000

    @staticmethod
    def _event_for_frame(frame, direction: FrameDirection) -> dict[str, Any] | None:
        bidirectional_frames = (
            VADUserStartedSpeakingFrame,
            VADUserStoppedSpeakingFrame,
            UserStartedSpeakingFrame,
            UserStoppedSpeakingFrame,
            BotStartedSpeakingFrame,
            BotStoppedSpeakingFrame,
            InterruptionFrame,
        )
        if (
            isinstance(frame, bidirectional_frames)
            and direction != FrameDirection.DOWNSTREAM
        ):
            return None

        if isinstance(frame, InterimTranscriptionFrame):
            return {"event": "caller_transcript", "text": frame.text, "final": False}
        if isinstance(frame, EagerTranscriptionFrame):
            return {
                "event": "caller_transcript",
                "text": frame.text,
                "final": False,
                "eager": True,
            }
        if isinstance(frame, TranscriptionFrame):
            return {
                "event": "caller_transcript",
                "text": frame.text,
                "final": True,
                "provider_finalized": frame.finalized,
            }
        if isinstance(frame, VADUserStartedSpeakingFrame):
            return {
                "event": "vad_speech_started",
                "threshold_seconds": frame.start_secs,
            }
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            return {
                "event": "vad_speech_stopped",
                "threshold_seconds": frame.stop_secs,
            }
        if isinstance(frame, UserStartedSpeakingFrame):
            return {"event": "caller_turn_started"}
        if isinstance(frame, UserStoppedSpeakingFrame):
            return {"event": "caller_turn_stopped"}
        if isinstance(frame, BotStartedSpeakingFrame):
            return {"event": "agent_speech_started"}
        if isinstance(frame, BotStoppedSpeakingFrame):
            return {"event": "agent_speech_stopped"}
        if isinstance(frame, InterruptionFrame):
            return {"event": "playback_interrupted"}
        if isinstance(frame, TTSSpeakFrame):
            return {"event": "agent_text", "text": frame.text}
        if isinstance(frame, TTSRequestedFrame):
            return {"event": "tts_requested", "text": frame.text}
        if isinstance(frame, LLMRequestStartedFrame):
            return {
                "event": "llm_request_started",
                "request_id": frame.request_id,
                "model": frame.model,
            }
        if isinstance(frame, LLMResponseFinishedFrame):
            return {
                "event": "llm_response_finished",
                "request_id": frame.request_id,
                "model": frame.model,
                "duration_ms": frame.duration_ms,
                "prompt_tokens": frame.prompt_tokens,
                "completion_tokens": frame.completion_tokens,
                "total_tokens": frame.total_tokens,
                "reasoning_tokens": frame.reasoning_tokens,
                "cached_tokens": frame.cached_tokens,
            }
        if isinstance(frame, LLMRequestFailedFrame):
            return {
                "event": "llm_request_failed",
                "request_id": frame.request_id,
                "model": frame.model,
                "duration_ms": frame.duration_ms,
                "error_type": frame.error_type,
                "error_message": frame.error_message,
            }
        if isinstance(frame, ToolCallStartedFrame):
            return {
                "event": "tool_call_started",
                "tool": frame.tool,
                "tool_call_id": frame.tool_call_id,
                "arguments": frame.arguments,
            }
        if isinstance(frame, ToolCallFinishedFrame):
            return {
                "event": "tool_call_finished",
                "tool": frame.tool,
                "tool_call_id": frame.tool_call_id,
                "duration_ms": frame.duration_ms,
                "status": frame.status,
                "result_summary": frame.result_summary,
                "error_type": frame.error_type,
                "error_message": frame.error_message,
            }
        if isinstance(frame, LLMTextFrame):
            return {"event": "agent_text_delta", "text": frame.text}
        if isinstance(frame, FunctionCallInProgressFrame):
            return {
                "event": "tool_call_started",
                "tool": frame.function_name,
                "tool_call_id": frame.tool_call_id,
                "arguments": frame.arguments,
            }
        if isinstance(frame, FunctionCallResultFrame):
            return {
                "event": "tool_call_finished",
                "tool": frame.function_name,
                "tool_call_id": frame.tool_call_id,
                "result": frame.result,
                "error": frame.error,
            }
        if isinstance(frame, FunctionCallCancelFrame):
            return {
                "event": "tool_call_cancelled",
                "tool": frame.function_name,
                "tool_call_id": frame.tool_call_id,
            }
        if isinstance(frame, ErrorFrame):
            return {
                "event": "pipeline_error",
                "error": frame.error,
                "fatal": frame.fatal,
                "processor": str(frame.processor) if frame.processor else None,
            }
        return None


class CallArtifacts:
    def __init__(self, meta: CallMeta, directory: Path):
        safe_call_id = re.sub(r"[^A-Za-z0-9_-]", "_", meta.call_id)
        self.directory = directory / safe_call_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self._meta = meta
        self._started_at_ns = time.monotonic_ns()
        self._metadata_path = self.directory / "metadata.json"
        self._timeline_path = self.directory / "timeline.jsonl"
        self._timeline_path.write_text("", encoding="utf-8")
        self.timeline = CallTimelineObserver(self._timeline_path, self._started_at_ns)
        self.recorder = self._create_recorder()
        self._write_metadata()

    def attach_turn_tracker(self, tracker: TurnTrackingObserver | None) -> None:
        if tracker is None:
            return

        @tracker.event_handler("on_turn_started")
        async def _on_turn_started(_tracker, turn_number: int):
            self.timeline.write("conversation_turn_started", turn=turn_number)

        @tracker.event_handler("on_turn_ended")
        async def _on_turn_ended(
            _tracker, turn_number: int, duration: float, interrupted: bool
        ):
            self.timeline.write(
                "conversation_turn_ended",
                turn=turn_number,
                duration_ms=round(duration * 1_000, 1),
                interrupted=interrupted,
            )

    def finish(self, outcome: str, metrics: dict[str, Any]) -> None:
        self._write_metadata(
            finished_at=datetime.now(self._meta.connected_at.tzinfo).isoformat(),
            outcome=outcome,
            metrics=metrics,
        )

    def _create_recorder(self) -> AudioBufferProcessor:
        recorder = AudioBufferProcessor(num_channels=2, auto_start_recording=True)

        @recorder.event_handler("on_audio_data")
        async def _on_audio_data(
            _recorder, audio: bytes, sample_rate: int, num_channels: int
        ):
            (self.directory / "stereo.wav").write_bytes(
                pcm_to_wav(audio, sample_rate, num_channels)
            )

        @recorder.event_handler("on_track_audio_data")
        async def _on_track_audio_data(
            _recorder,
            caller_audio: bytes,
            agent_audio: bytes,
            sample_rate: int,
            _num_channels: int,
        ):
            (self.directory / "caller.wav").write_bytes(
                pcm_to_wav(caller_audio, sample_rate, 1)
            )
            (self.directory / "agent.wav").write_bytes(
                pcm_to_wav(agent_audio, sample_rate, 1)
            )
            (self.directory / "mixed.wav").write_bytes(
                pcm_to_wav(mix_audio(caller_audio, agent_audio), sample_rate, 1)
            )
            seconds = len(caller_audio) / (sample_rate * 2)
            logger.info(
                "call debug bundle written | call_id={} path={} seconds={:.1f}",
                self._meta.call_id,
                self.directory,
                seconds,
            )

        return recorder

    def _write_metadata(self, **updates: Any) -> None:
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "call_id": self._meta.call_id,
            "stream_sid": self._meta.stream_sid,
            "from_number": self._meta.from_number,
            "connected_at": self._meta.connected_at.isoformat(),
            "audio": {
                "encoding": "PCM signed 16-bit little-endian",
                "caller": "caller.wav",
                "agent": "agent.wav",
                "mixed": "mixed.wav",
                "stereo": "stereo.wav (caller left, agent right)",
            },
            "timeline": "timeline.jsonl",
            **updates,
        }
        self._metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def create_call_artifacts(meta: CallMeta) -> CallArtifacts | None:
    directory = os.getenv(RECORDINGS_DIR_VAR, "").strip()
    if not directory:
        return None
    return CallArtifacts(meta, Path(directory))
