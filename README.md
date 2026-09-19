# claudios-prosper-ai

## Deepgram STT smoke test

Put the Deepgram key in `.env` at the repository root:

```dotenv
DEEPGRAM_API_KEY=your-key
```

Then stream a local audio file through Pipecat's Twilio decoder and Deepgram:

```shell
uv run python scripts/stt_smoke.py path/to/audio.wav
```

The smoke test requires `ffmpeg` on `PATH`.

## Transcript server

Start the production transport with per-call Deepgram STT and user-turn
aggregation:

```shell
PYTHONPATH=src uv run python -m agent
```

Each completed caller turn is emitted from Pipecat's `on_user_turn_stopped`
event and currently logged with its `call_id`. This server is the transcript
boundary for the future LLM layer; it intentionally has no TTS response yet.

## Twilio transport

`src/twilio/` is the WebSocket server the harness dials. It owns the wire and
the call's identity, and knows nothing about STT, TTS or the agent.

Plug the rest of the pipeline in with an `AgentFactory` — it receives one
call's `CallMeta` and returns the processors that sit between transport input
and output:

```python
from twilio import CallMeta, create_app


async def build_agent(meta: CallMeta):
    # meta.call_id      goes in every /api/v1/submit/* request
    # meta.from_number  may be None; a hint, never an identification
    # meta.connected_at Europe/Madrid; relative dates resolve against it
    return [stt, agent, tts]


app = create_app(build_agent)
```

Audio crosses that seam as PCM16 mono: 16 kHz in, 24 kHz out. The 8 kHz mu-law
on the wire is the serializer's problem.

### Testing it without the harness

`scripts/fake_harness.py` plays the caller side of Twilio Media Streams, so the
transport can be exercised before there is an API key to dial the real one.
Run the server with the echo agent, which needs no STT or TTS:

```shell
PYTHONPATH=src uv run python -m twilio
```

For an interactive local call, open [http://localhost:7860](http://localhost:7860)
in a browser after starting the server. It uses your microphone and sends the
same `connected`, `start`, `media`, and `stop` messages that Twilio Media
Streams sends to `/ws`. Use headphones to prevent the agent's playback from
feeding back into the microphone.

The page also includes a read-only local database viewer. To see the sample
storage data, seed it and point the server at that SQLite file:

```shell
PYTHONPATH=src uv run python scripts/storage_smoke.py
DATABASE_URL=sqlite+aiosqlite:///data/storage-smoke.db PYTHONPATH=src uv run python -m twilio
```

```shell
uv run python scripts/fake_harness.py --out reply.wav
uv run python scripts/fake_harness.py --withhold-caller-id
uv run python scripts/fake_harness.py --concurrency 20
```

Each call reports `first_reply_seconds` — silence is attributed to us and fails
the case — and `realtime_factor`, which should sit near 1.0.

### Exposing it

```shell
ngrok http --region eu 7860
```

Set the endpoint on the dashboard under **Settings → Integration**. It is
`wss://<host>/ws`: the scheme and the path are both part of it, and the change
applies to the *next* run.

### What this harness is not

- **No Twilio account.** `TwilioFrameSerializer` defaults to `auto_hang_up=True`
  and raises without REST credentials. This also rules out Pipecat's
  `runner.utils.create_transport`, which passes empty ones from the environment.
- **`clear` is ignored.** Pipecat emits it on every interruption; real Twilio
  flushes its playback buffer, the harness does not. Barge-in is won by
  dropping queued audio locally, never by anything sent on the wire.
- **The call ends when the socket closes.** Nothing tears the pipeline down on
  its own, hence the `on_client_disconnected` handler.

## Call console

The dashboard lives in `src/frontend/` (React + Vite + Tailwind) and is served
by the same FastAPI app at `/app`. Events reach it through
`src/observability/`: a non-blocking bus, SQLite behind it, and a WebSocket at
`/api/live`.

```shell
uv run python -m twilio                 # serves the built console at /app
cd src/frontend && npm run dev          # hot reload on :5173, proxies /api
```

`src/agent/` does not exist yet, so nothing writes a patient, an outcome or a
transcript. To look at the console with those filled in, seed a scratch
database — it never touches `calls.db`:

```shell
uv run python scripts/seed_console.py demo.db
CALLS_DB=demo.db uv run python -m twilio
```

The agent layer fills the console by importing three functions; no signature
anywhere needs to change:

```python
from observability import emit, update_call

emit(call_id, "tool_result", {"name": "search_patient", "response": body, "ms": 142})
update_call(call_id, patient_id="P00042", patient_name="Marta Ruiz", insurer="sanitas")
```
