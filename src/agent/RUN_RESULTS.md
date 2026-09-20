# Scored run snapshot — 20 September 2026

This branch preserves the working agent that reached **172/172 available points** on Prosper: all 16 scored problems reached their caps. Switchboard is unscored. The score credits the first four successful cases per problem; it does not mean every attempted call passed.

The final three batches were No Slot Free (run 92), Languages (run 93), and Questions (run 94). Each passed 3/4; settled scores were 8/8, 12/12, and 12/12 respectively. No calls remained active at the end.

## What changed

- Graph prompts negotiate valid alternatives, preserve booking consent, recover identification with a new identifier, handle third-party callers and published triage rules, and describe escalation as recording a request rather than a live transfer.
- Read-only catalogue results are deduplicated when rendering model context. Provider, latency, usage and available cost metadata are recorded.
- Speech guards suppress the observed internal-reasoning leak; dates are formatted for speech.
- Nearest-site availability uses CartoCiudad geocoding and published clinic coordinates, respecting specialty, coverage and time-window constraints.
- Reply language is selected from complete caller turns, preserving it through names and IDs. Availability can filter by requested provider language.
- Clinic answers include complete requested lists and clear doctor/site names.
- Local text and voice simulation scripts capture submissions without sending real clinic writes; voice scenario usage is documented in `scripts/simulation_cases/README.md`.

## Model settings used for these runs

Use the project's normal environment setup with your own credentials. These are the non-secret LLM settings from the successful runs:

```dotenv
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=google/gemini-3.8-flash
OPENAI_REASONING_EFFORT=low
OPENROUTER_PROVIDER=google-ai-studio
```

`OPENAI_API_KEY` must be an OpenRouter credential for that configuration. Provider routing is pinned with fallbacks disabled. STT is Deepgram Nova-3 multi; TTS is Cartesia Sonic 3.6.

## Known limitations

- A Questions caller still rejected correct GP names and hung up; the hidden caller interpretation is unavailable. Extra site information in that answer is a possible contributor, not a proven cause.
- Language detection is a conservative English/Spanish/Catalan heuristic. Nova-3 multi has no native Catalan mode, and Sonic 3.6 has no native Catalan voice. Catalan text uses the Spanish voice; recognition and pronunciation remain imperfect.
- The speech guard catches the demonstrated leak patterns, not every possible prompt leak. Escalation records an action; it does not establish a live human bridge.
- Runtime edits reload the development server. Wait until calls end before editing watched source files.

## Sharing and validation

Source, prompts, tests and simulation scenarios are committed. Local `.env`, databases, logs, generated transcripts, recordings and `outputs/` stay ignored. The score breakdown below contains no caller data.

- `uv run pytest -q`: 238 passed (three dependency deprecation warnings).
- `npm run build` in `src/frontend`: passed.
- Ruff on every changed/new Python file: passed.
- Repo-wide `uv run ruff check .`: one import-formatting finding in unchanged `scripts/agents.py`.
- Repo-wide `uv run ty check`: 52 diagnostics remain, including test-double annotations and application typing. This snapshot does not claim a clean repository-wide type check.
- Credential-value scan of tracked and unignored project files: no local credential values found.
- `git diff --check`: passed.

| Problem | Score |
| --- | ---: |
| The Simple Booking | 4/4 |
| The Doctor and the Site | 8/8 |
| The New Patient | 8/8 |
| When Exactly | 8/8 |
| The Rules | 12/12 |
| No Slot Free | 8/8 |
| Change and Cancel | 8/8 |
| The Third Party | 12/12 |
| Triage | 12/12 |
| Languages | 12/12 |
| Noise | 12/12 |
| The Difficult Caller | 16/16 |
| Adversarial and Privacy | 16/16 |
| The Wild Card | 12/12 |
| The Nearest Site | 12/12 |
| The Questions | 12/12 |
