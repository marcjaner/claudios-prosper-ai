# Call console

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
