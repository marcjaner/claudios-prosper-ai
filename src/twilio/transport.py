import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import Frame, OutputAudioRawFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner

from observability import emit, update_call

from .handshake import CallMeta, read_handshake
from .recording import create_call_artifacts
from .serializer import create_serializer

PIPELINE_SAMPLE_RATE = 16_000
TTS_SAMPLE_RATE = 24_000
# 20 ms out, matching the frames the harness sends in.
AUDIO_OUT_10MS_CHUNKS = 2
IDLE_TIMEOUT_SECONDS = 300
MAX_CALL_SECONDS = 300

# Builds the processors between transport input and output: STT, turn
# detection, the agent, TTS. Called once per call; nothing it returns is shared.
AgentFactory = Callable[[CallMeta], Awaitable[Sequence[FrameProcessor]]]


@dataclass
class CallMetrics:
    call_id: str
    started_at: float
    first_audio_out_at: float | None = None
    audio_out_bytes: int = 0

    @property
    def time_to_first_audio(self) -> float | None:
        if self.first_audio_out_at is None:
            return None
        return round(self.first_audio_out_at - self.started_at, 3)

    def summary(self) -> dict:
        # Silence from us is attributed to us and fails the case, so time to
        # first audio is the number worth watching on every call.
        return {
            "call_id": self.call_id,
            "duration_seconds": round(time.monotonic() - self.started_at, 3),
            "time_to_first_audio_seconds": self.time_to_first_audio,
            "audio_out_seconds": round(self.audio_out_bytes / (TTS_SAMPLE_RATE * 2), 3),
        }

    def log(self) -> None:
        summary = self.summary()
        if self.first_audio_out_at is None:
            logger.error("call said nothing | {}", json.dumps(summary))
        else:
            logger.info("call finished | {}", json.dumps(summary))


class OutboundAudioTap(FrameProcessor):
    def __init__(self, metrics: CallMetrics):
        super().__init__()
        # Not self._metrics: FrameProcessor owns that name for its own
        # metrics and overwriting it makes the processor unusable at setup.
        self._call_metrics = metrics

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, OutputAudioRawFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            logger.debug("outbound audio frame | call_id=%s bytes=%s", self._call_metrics.call_id, len(frame.audio))
            if self._call_metrics.first_audio_out_at is None:
                self._call_metrics.first_audio_out_at = time.monotonic()
                update_call(self._call_metrics.call_id, state="speaking")
            self._call_metrics.audio_out_bytes += len(frame.audio)
        await self.push_frame(frame, direction)


def create_transport(websocket, meta: CallMeta) -> FastAPIWebsocketTransport:
    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        serializer=create_serializer(meta.stream_sid, meta.call_id),
        audio_out_10ms_chunks=AUDIO_OUT_10MS_CHUNKS,
        # The harness is not a browser and sends no Origin header. Empty
        # allows all; leaving it to the default would let
        # PIPECAT_ALLOWED_ORIGINS in the environment reject every call.
        allowed_origins=[],
    )
    return FastAPIWebsocketTransport(websocket=websocket, params=params)


def register_initial_greeting(
    worker: PipelineWorker, initial_greeting: str | None
) -> None:
    if not initial_greeting:
        return

    @worker.event_handler("on_pipeline_started")
    async def _on_pipeline_started(worker, _frame):
        await worker.queue_frame(
            TTSSpeakFrame(initial_greeting, append_to_context=False)
        )


async def run_call(
    websocket,
    build_agent: AgentFactory,
    *,
    initial_greeting: str | None = None,
) -> None:
    await websocket.accept()

    try:
        meta = await read_handshake(websocket)
    except Exception as error:  # noqa: BLE001 - a bad handshake fails one call, not the wave
        logger.error("handshake failed: {}", error)
        await websocket.close()
        return

    logger.info(
        "call started | call_id={} from_number={}",
        meta.call_id,
        meta.from_number or "<withheld>",
    )
    metrics = CallMetrics(call_id=meta.call_id, started_at=time.monotonic())
    artifacts = None
    outcome = "completed"
    failure: str | None = None
    # connected_at is wall clock; CallMetrics.started_at is monotonic and means
    # nothing to a browser drawing a duration ring.
    update_call(
        meta.call_id,
        started_at=meta.connected_at.timestamp(),
        state="connected",
        from_number=meta.from_number,
    )

    try:
        transport = create_transport(websocket, meta)
        agent = await build_agent(meta)
        artifacts = create_call_artifacts(meta)
        # After transport.output(), where both directions of audio pass.
        pipeline = Pipeline(
            [
                transport.input(),
                *agent,
                OutboundAudioTap(metrics),
                transport.output(),
                *([artifacts.recorder] if artifacts else []),
            ]
        )
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=PIPELINE_SAMPLE_RATE,
                audio_out_sample_rate=TTS_SAMPLE_RATE,
            ),
            idle_timeout_secs=IDLE_TIMEOUT_SECONDS,
            cancel_on_idle_timeout=True,
            cancel_runner_on_idle_timeout=False,
            observers=[artifacts.timeline] if artifacts else None,
        )
        if artifacts:
            artifacts.attach_turn_tracker(worker.turn_tracking_observer)

        # Nothing tears the pipeline down when the caller hangs up. Without
        # this the worker lives until the idle timeout, holding a socket and a
        # pipeline for minutes, ten at a time during a Run All.
        @transport.event_handler("on_client_disconnected")
        async def _on_disconnect(_transport, _client):
            await worker.cancel(reason="caller hung up")

        register_initial_greeting(worker, initial_greeting)
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await asyncio.wait_for(runner.run(), timeout=MAX_CALL_SECONDS)
    except TimeoutError:
        outcome = "timeout"
        failure = f"call exceeded {MAX_CALL_SECONDS}s"
        logger.error("call exceeded {}s | call_id={}", MAX_CALL_SECONDS, meta.call_id)
    except Exception as error:  # noqa: BLE001 - one call must never take down the others
        outcome = "failed"
        failure = str(error)
        logger.exception("call failed | call_id={}", meta.call_id)
    finally:
        if artifacts:
            artifacts.finish(outcome, metrics.summary())
        metrics.log()
        # Teardown runs under cancellation, so anything that must be recorded
        # belongs here rather than in a pipeline event handler.
        update_call(
            meta.call_id,
            ended_at=time.time(),
            state="ended",
            ttfa_seconds=metrics.time_to_first_audio,
            error=failure,
        )
        emit(meta.call_id, "call_ended", {"error": failure})
