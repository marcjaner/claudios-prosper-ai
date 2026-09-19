# Agent builder — v1

A graph interface for configuring the call agent. Each node is a **stage** with its own prompt
and its own allowed tools. Each edge is a **gated transition**: the agent may only move along it
once the facts it requires are known.

Three goals, in order: **configurable** (author the whole graph in the UI), **traceable** (for any
call, see which stage it was in, what facts it held, and why a transition or tool call was
refused), **usable** (a receptionist flow is a five-minute job, not a config exercise).

## Primitives

- **Node** — a prompt, a list of allowed tool names, and an optional list of facts it clears on entry.
- **Edge** — a target node and a list of required fact keys.
- **Facts** — a flat `{key: value}` dict that accumulates over the call.

Nothing here is clinic-specific.

## Runtime

### Graph operations are tools

The agent already uses native function calling, so the graph exposes its own operations the same
way, injected into every stage:

- `record_facts(facts)` — a flat mapping of string keys to string values, structurally validated
  before anything is mutated.
- `go_to(stage)` — offered whenever the stage has any outgoing edge at all, not only satisfiable
  ones. The runtime validates the target against the facts at execution time and refuses
  otherwise. Hiding the tool would make it impossible to record a fact and move on it in the same
  breath, and a visible refusal is better trace than an absent option.

Putting these in a structured-JSON response instead would mean migrating the planning completion
off native calls, and `complete_structured` parses only `message.content` — a reply carrying
native calls and no text fails validation outright.

### One completion, one frozen snapshot

Every argument in a batch was generated before any of its tools ran, so ordering inside a batch
cannot let a fact write consume a result from that same batch. The contract:

1. Freeze the source stage and its advertised tools for the whole completion.
2. Validate the entire batch against that snapshot.
3. Execute permitted business tools.
4. Apply fact writes.
5. Commit at most one transition, last.

Destination tools become available on the next completion, never mid-batch.

### A caller turn is a bounded loop

Today a turn is plan → tools → one tool-free follow-up → stop, so a stage that learns the patient
ID and moves on would sit there until the caller spoke again.

A turn is **at most 4 action steps plus 1 reserved tool-free answer**, and the budget resets on
every caller turn. The reserved final step exists because an action step's text is generated
before its own results: a booking on the last action step would otherwise never be confirmed out
loud.

**The loop continues only when a step produced something new** — a business tool ran, or the
stage changed. A step that only writes facts has produced nothing the model has not already seen,
so the turn ends there and the agent waits for the caller. That covers the natural "what day
suits you?" plus a quiet `record_facts`, and it stops a model burning the budget rewriting the
same fact. Every turn records why it ended: waiting, budget exhausted, or failed.

### Scope is enforced before the agent speaks

Speech reaches TTS before tools run, so a refusal afterwards cannot retract "I'll book that now".
The whole draft utterance is therefore suppressed whenever **any** requested operation is
refused — a forbidden tool, or a `go_to` whose requirements are unmet — and the refusal is fed
back so the next completion can explain or recover. Suppressing only when everything was refused
would keep the false promise whenever one harmless call survived.

Control-only steps stay silent: the existing "Let me check that for you" filler must not be
invented for a step that merely recorded a fact. Refused operations never reach
`record_submission`, and neither do successful graph operations — a submission log is for real
clinic actions.

### State lives on the call, and so does the graph

The graph is read once at call start and frozen for that call's lifetime, so saving in the
builder cannot move a live call's ground. Current stage, facts, and the conversation history live
on the same per-call object.

### The prompt is assembled, not dumped

Shared preamble + stage prompt + current facts + history + **every outgoing transition with its
required keys and which of them are still missing**. Without those key names nothing tells the
model whether to record `patient_id`, `patientId` or `identified_patient`; showing blocked edges
too is generic graph metadata, not a fact schema.

History is kept deliberately on the per-call object: caller text, accepted assistant speech, and
for each operation its name, arguments, status and result. `memory_for_call` serializes every
repository event and would feed graph bookkeeping back as if it were conversation — but dropping
it without a replacement would remove the only cross-turn record of what tools returned.

The shared preamble also needs two new rules: never repeat a submission that already succeeded,
and never narrate graph operations to the caller.

### Failures

Tool exceptions become results the loop can feed back, never an escape that kills the turn.
Submissions are never retried automatically — a timeout after sending one does not prove nothing
happened. An unrecoverable error ends the turn with something the caller can hear.

## What the graph does and does not guarantee

It guarantees **tool scope per stage**: a node cannot call a tool it was not given. That is
structural, not prompt discipline.

It does **not** prove a fact is true. A model can write `patient_id: "unknown"` and unlock an
edge. Checking that a patient ID came from a real directory lookup is clinic logic and belongs
beside the clinic tools, not inside a generic graph engine. Acceptance criteria are written
against tool scope, not identification.

## Corrections

Facts only ever accumulate, which breaks on a caller who handles her grandson's appointment and
then her own — stale slot and policy facts would keep presence checks passing. A node may
declare `clears`, a list of fact keys dropped when it is entered. One field, no framework. It only fires
on entry, so a correction has to route back through the stage that resets it — staying put and
overwriting the patient will not clear the stale slot.

## Graph file

```json
{
  "entry": "identify",
  "nodes": [
    {
      "id": "identify",
      "prompt": "Identify the caller...",
      "tools": ["get_clinic_catalogue", "search_patients"],
      "clears": [],
      "position": { "x": 0, "y": 0 }
    }
  ],
  "edges": [{ "from": "identify", "to": "book", "requires": ["patient_id"] }]
}
```

`position` is for the canvas only. Saving validates before replacing: entry exists, node ids
unique, every edge endpoint resolves, every tool name is real. Written atomically. A shape check
alone would not catch a dangling edge, and an editor mistake must not break the next call.

The default graph is today's agent: one stage, all ten tools, no edges. Its stage prompt is empty
because the shared preamble is already `prompts.yaml`; duplicating it into the node would send the
same instructions twice.

## Work

Backend
- `src/agent/graph.py` — models, load/save, validation.
- `src/agent/stage_runtime.py` — per-call state, prompt assembly, the step loop, scope enforcement.
- `run_agent_turn` rewritten around the loop; per-call state and graph created in `transcription.py`.
- `src/agent/graph_api.py` — `GET`/`PUT /api/graph`, `GET /api/tools`.

Frontend
- `#/builder`: React Flow canvas, add/delete stages and transitions, side panel for prompt, tool
  checkboxes, `requires`, `clears`, entry selection.
- Trace panel on the call detail view: stage timeline, facts with provenance, rejections.

## Done when

- The default one-node graph behaves like today's agent.
- Positive path: a caller who gives identity and intent together gets identified and progresses
  to availability within one turn, without an extra caller utterance.
- Negative path: a premature `book_appointment` in the identify stage is dropped before anything
  is spoken, appears in the trace as `tool_rejected`, and is not recorded as a submission.
- Saving a graph mid-call does not disturb the call in progress.
