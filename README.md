# claudios-prosper-ai

The existing clinic console backed by the Virtual Agents voice receptionist.

## Setup

Install Python, agent, and frontend dependencies:

```shell
uv sync
npm --prefix virtual-agents install
npm --prefix src/frontend install
```

Create `.env` at the repository root. The agent accepts the existing
`PLATFORM_API_BASE_URL` and `PLATFORM_API_KEY` names as aliases for the agent's
`PROSPER_*` variables.

```dotenv
PLATFORM_API_BASE_URL=https://hackspain.getprosperapp.com
PLATFORM_API_KEY=pk-...

AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=gpt-realtime-1.5

# Optional: enables the existing live and final Jev conversation scoring.
TYPESAFE_API_KEY=...
TYPESAFE_DEFAULT_MODEL=jev-latest
```

See [`virtual-agents/.env.example`](virtual-agents/.env.example) for optional voice,
telemetry, recording, and GPT-Live settings.

## Run

```shell
./run.sh
```

Virtual Agents is the default runtime. To fall back to the existing Python
agent while keeping the same port, dashboard, ngrok tunnel, and Prosper
integration:

```shell
AGENT_RUNTIME=python ./run.sh
```

Stop the current runtime before switching. Set `AGENT_RUNTIME=virtual-agents`
explicitly to switch back, or omit it to use the default.

Both runtimes expose the voice endpoint at `ws://localhost:7860/ws` and the
clinic console at `http://localhost:7860/app/`. Virtual Agents exposes health
at `http://localhost:7860/healthz`; the Python agent uses
`http://localhost:7860/health`. Set `PORT` to use another port.

For the public challenge endpoint, expose the same port and configure
`wss://<host>/ws` in Prosper. No authorization header is currently required.
