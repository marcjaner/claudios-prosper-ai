import asyncio

import numpy as np
import websockets
from fake_harness import place_call, synthesize_caller_audio
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner
from test_twilio_transport import BackgroundServer

from agent.operator import LINE_OPEN, register_operator_api
from observability import CallUpdate, Store
from twilio import takeover
from twilio.echo import build_echo_agent
from twilio.server import create_app
from twilio.takeover import OPERATOR_SAMPLE_RATE, AgentMute, OperatorBridge


class Collector(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


def caller_audio(payload: bytes = b"\x01\x02" * 160) -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=payload, sample_rate=16_000, num_channels=1)


def tts_audio() -> TTSAudioRawFrame:
    return TTSAudioRawFrame(audio=b"\x00" * 64, sample_rate=24_000, num_channels=1)


async def run_pipeline(processors, scenario):
    collector = Collector()
    worker = PipelineWorker(
        Pipeline([*processors, collector]), cancel_on_idle_timeout=False
    )
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def _started(_worker, _frame):
        started.set()

    async def drive():
        await asyncio.wait_for(started.wait(), timeout=2)
        await scenario(worker)
        await asyncio.sleep(0.1)
        await worker.queue_frame(EndFrame())

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), drive())
    return collector.frames


def test_caller_audio_reaches_the_agent_until_someone_takes_over():
    bridge = OperatorBridge()

    async def scenario(worker):
        await worker.queue_frame(caller_audio())

    frames = asyncio.run(run_pipeline([bridge], scenario))
    assert any(isinstance(frame, InputAudioRawFrame) for frame in frames)
    assert bridge.caller_audio.empty()


def test_taking_over_diverts_the_caller_and_interrupts_the_agent():
    bridge = OperatorBridge()
    mute = AgentMute(bridge)

    async def scenario(worker):
        await bridge.take_over()
        await worker.queue_frame(caller_audio(b"\x07\x07" * 160))
        await worker.queue_frame(tts_audio())
        await bridge.speak(b"\x09\x09" * 160)

    frames = asyncio.run(run_pipeline([bridge, mute], scenario))

    assert any(isinstance(frame, InterruptionFrame) for frame in frames)
    assert not any(isinstance(frame, InputAudioRawFrame) for frame in frames)
    assert not any(isinstance(frame, TTSAudioRawFrame) for frame in frames)
    spoken = [frame for frame in frames if isinstance(frame, OutputAudioRawFrame)]
    assert [frame.audio for frame in spoken] == [b"\x09\x09" * 160]
    assert spoken[0].sample_rate == OPERATOR_SAMPLE_RATE

    # The operator hears the caller, then learns the call is over.
    assert bridge.caller_audio.get_nowait() == b"\x07\x07" * 160
    assert bridge.caller_audio.get_nowait() is None


def test_the_hand_off_waits_until_the_caller_has_heard_the_agent_out(monkeypatch):
    monkeypatch.setattr(takeover, "HANDOFF_TIMEOUT_SECONDS", 5)
    bridge = OperatorBridge()
    order = []

    async def scenario(worker):
        async def hand_over():
            await bridge.take_over(after_handoff=True)
            order.append("active")

        handover = asyncio.create_task(hand_over())
        await asyncio.sleep(0.1)
        assert not bridge.active
        order.append("stopped speaking")
        await bridge.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await asyncio.wait_for(handover, timeout=1)

    asyncio.run(run_pipeline([bridge], scenario))
    assert order == ["stopped speaking", "active"]


def test_operator_hears_the_caller_and_the_caller_hears_the_operator(monkeypatch):
    # No platform key: the escalation is skipped and logged, never fatal.
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    monkeypatch.delenv("PLATFORM_API_BASE_URL", raising=False)
    # The echo agent has no TTS, so nothing ever reports the hand-off as spoken.
    monkeypatch.setattr(takeover, "HANDOFF_TIMEOUT_SECONDS", 0.3)
    app = create_app(build_echo_agent)
    register_operator_api(app)
    operator_tone = (b"\x40\x1f" * 320) * 25  # half a second at 16 kHz

    async def run():
        async with BackgroundServer(app) as server:
            call = asyncio.create_task(
                place_call(
                    server.ws_url, synthesize_caller_audio(6.0), linger_seconds=0
                )
            )
            for _ in range(100):
                if app.state.active_calls:
                    break
                await asyncio.sleep(0.05)
            (call_id,) = app.state.active_calls
            await asyncio.sleep(0.5)

            url = f"ws://127.0.0.1:{server.port}/api/calls/{call_id}/operator"
            async with websockets.connect(url) as operator:
                opened = await asyncio.wait_for(operator.recv(), timeout=2)
                heard = await asyncio.wait_for(operator.recv(), timeout=2)
                for offset in range(0, len(operator_tone), 640):
                    await operator.send(operator_tone[offset : offset + 640])
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.5)
            result = await asyncio.wait_for(call, timeout=10)
            await asyncio.sleep(0.2)
            return call_id, opened, heard, result, app.state.store.get_call(call_id)

    call_id, opened, heard, result, stored = asyncio.run(run())

    assert opened == LINE_OPEN
    assert isinstance(heard, bytes) and len(heard) > 0
    kinds = [event["kind"] for event in app.state.store.get_events(call_id)]
    assert (
        kinds.index("tts")
        < kinds.index("operator_takeover")
        < kinds.index("operator_hangup")
    )
    assert stored["handled_by"] == "operator"
    assert stored["ended_at"] is not None
    assert call_id not in app.state.active_calls
    # The caller's synthetic voice is zero-mean; the operator sent a positive
    # DC tone, so a window with a strongly positive mean can only be theirs.
    received = np.frombuffer(result.pcm_received, dtype="<i2").astype(float)
    windows = received[: len(received) // 400 * 400].reshape(-1, 400)
    assert len(windows) > 0
    assert windows.mean(axis=1).max() > 3000


def test_handled_by_survives_the_store(tmp_path):
    store = Store(tmp_path / "calls.db")
    store.write([CallUpdate("CA1", {"started_at": 1.0})])
    store.write([CallUpdate("CA1", {"handled_by": "operator", "state": "operator"})])
    assert store.get_call("CA1")["handled_by"] == "operator"
    store.close()
