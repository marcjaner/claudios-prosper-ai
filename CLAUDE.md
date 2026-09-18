# claudios-prosper-ai

Hackathon project (HackSpain, Prosper's "El Turno" challenge, 18–20 Sep 2026). A voice AI agent that answers inbound scheduling calls for a clinic like a receptionist: it identifies the caller against the clinic's read-only EHR, finds real availability, and books, reschedules, cancels, refuses, or escalates — then POSTs the action it took back to the platform.

Built on pipecat: Twilio Media Streams (WebSocket) → STT → agent/LLM + tool calls → TTS → Twilio. Correctness of the submitted record is what scores; the voice quality and platform around it are judged separately.

## Mindset

This is a hackathon. **Ship working code fast; favor simplicity over completeness.**

- Prefer the simplest solution that works. No speculative abstractions, config layers, or plugin systems.
- Apply YAGNI aggressively — build only what the current demo needs.
- Don't add tests, error handling, or docs for code paths the demo won't hit unless asked.

## Clean code

- SRP / DRY / KISS: each function does one thing, no copy-paste, simplest approach wins.
- Names reveal intent (`userCount`, `isActive`, `getUserById`); if a name needs a comment, rename it.
- Small functions, guard clauses over nesting, max ~3 args. Let code self-document — skip obvious comments.
- Named constants over magic numbers.

## Stack & tooling

- Python ≥3.12, managed with **uv** (`uv run`, `uv sync`).
- Lint/format: **ruff**. Types: **ty**. Tests: **pytest**. Hooks: pre-commit.
- Layout: `src/{agent,stt,tts,twilio,frontend}/`. Prompts live in `prompts/*.yaml`.

## Challenge reference

`instructions/` has the full challenge context: `general.md` (rules, clinic, scoring, the 18 problems), `api.md` (platform + EHR endpoints), `diagram.md` (pipeline flow).
