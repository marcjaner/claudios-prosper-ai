import asyncio

import numpy as np
import uvicorn
from fake_harness import place_call, synthesize_caller_audio

from twilio.echo import build_echo_agent
from twilio.server import create_app

ONE_SECOND_OF_SAMPLES = 8_000


class BackgroundServer:
    def __init__(self, app):
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        )
        self._task: asyncio.Task[None] | None = None
        self.port = None

    async def __aenter__(self):
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(200):
            if self._server.started and self._server.servers:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("server never started")
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.should_exit = True
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=10)

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"


def call_the_echo_agent(seconds: float, **kwargs):
    async def run():
        async with BackgroundServer(create_app(build_echo_agent)) as server:
            return await place_call(
                server.ws_url, synthesize_caller_audio(seconds), **kwargs
            )

    return asyncio.run(run())


def test_audio_survives_the_round_trip():
    sent_pcm = synthesize_caller_audio(2.0)

    async def run():
        async with BackgroundServer(create_app(build_echo_agent)) as server:
            return await place_call(server.ws_url, sent_pcm)

    result = asyncio.run(run())

    assert result.ok, result.error
    sent = np.frombuffer(sent_pcm, dtype="<i2").astype(float)
    received = np.frombuffer(result.pcm_received, dtype="<i2").astype(float)
    length = min(len(sent), len(received))
    assert length > ONE_SECOND_OF_SAMPLES

    a = sent[:length] - sent[:length].mean()
    b = received[:length] - received[:length].mean()
    correlation = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    assert correlation > 0.95, f"reply does not match what was sent ({correlation:.3f})"


def test_first_audio_is_prompt():
    result = call_the_echo_agent(1.5)

    assert result.ok, result.error
    # A processor broken at setup once pushed this to 4.4s while the call
    # still "worked", which on a scored call reads as a slow agent.
    assert result.first_reply_seconds is not None
    assert result.first_reply_seconds < 2.0


def test_outbound_audio_is_paced_like_a_phone_line():
    result = call_the_echo_agent(3.0, linger_seconds=4.0)

    assert result.ok, result.error
    assert 0.7 < result.realtime_factor < 1.5


def test_withheld_caller_id_is_served():
    result = call_the_echo_agent(1.0, from_number=None)

    assert result.ok, result.error
    assert result.frames_received > 0


def test_ten_concurrent_calls():
    async def run():
        async with BackgroundServer(create_app(build_echo_agent)) as server:
            pcm = synthesize_caller_audio(1.5)
            return await asyncio.gather(
                *(place_call(server.ws_url, pcm) for _ in range(10))
            )

    results = asyncio.run(run())

    assert all(r.ok for r in results), [r.error for r in results if not r.ok]
    assert all(r.frames_received > 0 for r in results)
    assert len({r.call_id for r in results}) == 10
