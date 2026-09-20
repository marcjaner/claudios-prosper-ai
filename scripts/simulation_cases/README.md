# Local voice simulations

Run a private audio call without taking the team's Prosper run slot:

```sh
uv run python scripts/simulate_call.py scripts/simulation_cases/child_gp_redirect.yaml
uv run python scripts/simulate_call.py scripts/simulation_cases/gp_review.yaml
uv run python scripts/simulate_call.py scripts/simulation_cases/age_refusal.yaml
```

Requires the project's normal `.env`, macOS `say`, and `ffmpeg`. Agent STT/TTS and
LLM calls still use the configured providers and consume their normal usage.
The simulated patient's voice uses macOS; its replies use the configured LLM.

The runner starts a separate server on a random localhost port. Databases, logs,
caller utterances, call recordings and `report.json` live under a new
`outputs/simulations/<run>/` directory, alongside the scenario and a snapshot of
the agent graph used for that run. It leaves the running public server alone.
Clinic reads use Prosper's read-only EHR; all six submission routes are captured
locally, with a second HTTP guard that blocks real writes.

The patient hears the agent's actual returned audio, transcribed independently
with Deepgram. It cannot read the agent's internal transcript, tools or expected
answer. It replies from the YAML facts and preferences. Both sides send real
8 kHz mu-law audio over the same Twilio-shaped protocol as Prosper.

Calls stop after **180 real seconds**, including speaking, model latency and
pauses. `--seconds 30` can exercise a shorter cutoff. The isolated agent also
receives the remaining 180-second budget. This does not change the live server's
current budget. A 30-second no-audible-reply timeout is a local heuristic;
Prosper's exact silence threshold is not available in the challenge document.

## Cases and reports

Copy a YAML example and change `caller.persona`, `caller.facts`, `language`, and
`voice`. `say -v '?'` lists installed voices; `Mónica` speaks Spanish. Keep
`accepted` separate: it contains one or more acceptable action lists and never
goes into the caller prompt. A correction or hesitation can be specified in the
persona; wording and compliance vary because the caller is an LLM.

The included booking answers are snapshots from the September 2026 public cases.
Update them when the date or clinic availability changes. `age_refusal` is a
custom variation, not a claim about a published Prosper case.

Cached public cases are also available:

```sh
uv run python scripts/simulate_call.py --list-public
uv run python scripts/simulate_call.py --public-case simple_booking-14a8720daa02
```

Only the simple-booking public summaries are currently imported automatically.
Other summaries omit important caller facts, especially registration,
third-party, triage, language and adversarial cases. The importer rejects these;
write an explicit YAML scenario instead. It also requires an unambiguous personal
directory match. It never uses accepted answers to fill in caller knowledge.

`report.json` contains what the patient actually heard, what it said, accepted
local actions, rejected submission attempts, timing, and expected actions.
`record_matches` compares the complete action list, preserving duplicate actions
and normalizing timezone-equivalent slots. Other fields compare exactly. Local
`passed` means the record matches; `completed_conversation` separately tells you
whether the conversation ended normally. A matching record is not marked wrong
merely because the caller failed to hang up cleanly. Local submissions are
strictly capped at the call deadline; Prosper allows a further 30 seconds after
socket closure for submission delivery.
This is not Prosper's complete scorer: no private persona reproduction,
registration normalization parity, privacy-leak checks, exact acoustic noise
bed or deliberate simultaneous speech. A local pass is a regression signal,
not an official score.
