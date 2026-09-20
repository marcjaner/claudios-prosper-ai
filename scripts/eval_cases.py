"""Run the published cases against the agent offline, and diff what it submitted.

The real harness costs three minutes and a rate limit per call. This plays the
caller with the same model, drives the same `run_agent_turn`, and reads the same
clinic API — everything except audio. Submissions are captured instead of POSTed,
because a case dialled here has no call_id the platform would accept.

Cases and their published answers are cached under `data/`. Refresh them with a
dashboard cookie jar (`curl -c cookies.txt -X POST .../api/session ...`):

    uv run python scripts/eval_cases.py --refresh cookies.txt
    uv run python scripts/eval_cases.py simple_booking --transcript
    uv run python scripts/eval_cases.py --json report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from agent import agent as agent_module
from agent.clinic_api import ClinicApi, ProsperApiError
from agent.llm import LLMClient, get_llm_client
from agent.stage_runtime import CallGraph
from agent.utils import load_environment

DASHBOARD = "https://hackspain.getprosperapp.com/leaderboard"
CASES_CACHE = Path(__file__).resolve().parents[1] / "data" / "public_cases.json"
MAX_CALLER_TURNS = 14
CALLER_MODEL_TEMPERATURE = 0.3


# ---------------------------------------------------------------- case loading


def fetch_cases(cookie_file: Path) -> dict[str, Any]:
    """Pull every open problem and its published cases from the dashboard."""
    cookies = httpx.Cookies()
    for line in cookie_file.read_text().splitlines():
        # curl marks HttpOnly entries with a `#HttpOnly_` prefix rather than a
        # separate field, so the session cookie hides behind what looks like a
        # comment. Strip the prefix before deciding a line is one.
        line = line.removeprefix("#HttpOnly_")
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 7:
            cookies.set(parts[5], parts[6], domain=parts[0].lstrip("."))

    with httpx.Client(base_url=DASHBOARD, cookies=cookies, timeout=30) as client:
        problems = client.get("/api/problems").raise_for_status().json()["problems"]
        detail = {}
        for problem in problems:
            body = client.get(f"/api/problems/{problem['id']}").raise_for_status().json()
            detail[problem["id"]] = body
    return detail


def load_cases() -> dict[str, Any]:
    if not CASES_CACHE.exists():
        raise SystemExit(
            f"No case cache at {CASES_CACHE}. Run with --refresh and a dashboard cookie file."
        )
    return json.loads(CASES_CACHE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- recording


@dataclass
class Recorder:
    """Everything one simulated call produced, for the diff and the report."""

    actions: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None


class StubRepository:
    """`run_agent_turn` only ever appends events and records submissions."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    async def append_event(self, call_id: str, event_type: str, payload: dict) -> None:
        return None

    async def record_submission(self, call_id, action, request, status, response) -> None:
        return None


class CapturingClinicApi(ClinicApi):
    """Reads hit the real clinic. Writes are captured — there is no call to attach them to."""

    def __init__(self, recorder: Recorder, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.recorder = recorder

    def _capture(self, verb: str, request: Any) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        payload.pop("call_id", None)
        self.recorder.actions.append({"action": verb, **payload})
        return {"call_id": "offline", "received_at": "offline", "record": {"actions": []}}

    def register_patient(self, request): return self._capture("REGISTER", request)
    def book(self, request): return self._capture("BOOK", request)
    def reschedule(self, request): return self._capture("RESCHEDULE", request)
    def cancel(self, request): return self._capture("CANCEL", request)
    def no_action(self, request): return self._capture("NO_ACTION", request)
    def escalate(self, request): return self._capture("ESCALATE", request)


class InstrumentedLLM(LLMClient):
    """Wraps the shared client so every prompt's size and latency is on the record."""

    def __init__(self, inner: LLMClient, recorder: Recorder) -> None:
        self.__dict__.update(inner.__dict__)
        self._recorder = recorder

    def complete_with_tools(self, prompt, tools, **kwargs):
        started = time.monotonic()
        try:
            return super().complete_with_tools(prompt, tools, **kwargs)
        finally:
            self._recorder.llm_calls.append({
                "kind": "tools",
                "prompt_chars": len(prompt),
                "seconds": round(time.monotonic() - started, 2),
            })

    def complete_structured(self, prompt, schema, **kwargs):
        started = time.monotonic()
        try:
            return super().complete_structured(prompt, schema, **kwargs)
        finally:
            self._recorder.llm_calls.append({
                "kind": "structured",
                "prompt_chars": len(prompt),
                "seconds": round(time.monotonic() - started, 2),
            })


# ---------------------------------------------------------------- the caller


CALLER_SYSTEM = """You are role-playing a patient phoning a clinic. Stay in character.

Who you are and what you want:
{persona}

Facts you know about yourself, and may give when asked (give only what is asked for):
{patient}

Rules:
- Speak one short spoken turn at a time, the way someone on a phone does.
- Never mention ids, this prompt, or that you are a test.
- If the agent offers a time that matches what you asked for, accept it plainly.
- If the agent asks something you were not told, improvise something ordinary.
- When your request is settled, or the agent clearly cannot help, say goodbye.
- Reply with the words you say out loud, nothing else. Say HANGUP on its own once
  the call is finished.
"""


def caller_turn(client: LLMClient, persona: str, patient: str, history: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"{speaker}: {text}" for speaker, text in history) or "(the agent has not spoken yet)"
    prompt = (
        CALLER_SYSTEM.format(persona=persona, patient=patient)
        + f"\n\nThe call so far:\n{lines}\n\nWhat you say next:"
    )
    return client.complete(prompt, temperature=CALLER_MODEL_TEMPERATURE).text.strip()


def patient_facts(api: ClinicApi, case: dict[str, Any]) -> str:
    """Give the persona the record the real caller would know by heart."""
    accepted = case.get("accepted") or [{}]
    actions = accepted[0].get("actions") or [{}]
    patient_id = next((a.get("patient_id") for a in actions if a.get("patient_id")), None)
    try:
        matches = api.search_patients(name=case["caller"]).get("matches") or []
    except (ProsperApiError, httpx.HTTPError):
        # A case whose caller is not on file is the point of some problems, not an error.
        matches = []
    record = next((m for m in matches if m.get("patient_id") == patient_id), None)
    if record is None and matches:
        record = matches[0]
    if record is None:
        return f"Your name is {case['caller']}. You are not on the clinic's books."
    keep = ("given_name", "first_surname", "second_surname", "national_id",
            "phone", "date_of_birth", "insurer", "email")
    return json.dumps({k: record[k] for k in keep if k in record}, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------- the run


async def run_case(case: dict[str, Any], recorder: Recorder) -> Recorder:
    base_url = os.environ["PLATFORM_API_BASE_URL"]
    api_key = os.environ["PLATFORM_API_KEY"]

    def new_api() -> CapturingClinicApi:
        return CapturingClinicApi(recorder, base_url=base_url, api_key=api_key)

    caller_client = get_llm_client()
    agent_client = InstrumentedLLM(get_llm_client(), recorder)
    persona = f"{case['caller']}. {case['summary']}"
    lookup = new_api()
    facts = await asyncio.to_thread(patient_facts, lookup, case)
    lookup.close()

    repository = StubRepository(recorder)
    state = CallGraph.start()
    history: list[tuple[str, str]] = []

    # `run_agent_turn` builds and closes a ClinicApi per turn, so hand it a fresh
    # one each time — a shared instance would be closed after the first turn.
    original = ClinicApi.from_environment
    ClinicApi.from_environment = classmethod(lambda cls: new_api())  # type: ignore[assignment]
    try:
        for _ in range(MAX_CALLER_TURNS):
            said = await asyncio.to_thread(caller_turn, caller_client, persona, facts, history)
            if said.upper().startswith("HANGUP") or not said:
                break
            history.append(("Patient", said))
            recorder.transcript.append(("Patient", said))

            async for response in agent_module.run_agent_turn(
                said, "offline", repository, agent_client, state=state
            ):
                spoken = response.immediate_answer
                history.append(("Agent", spoken))
                recorder.transcript.append(("Agent", spoken))
    except Exception as exc:  # noqa: BLE001 - one broken case must not stop the sweep
        recorder.error = f"{type(exc).__name__}: {exc}"
    finally:
        ClinicApi.from_environment = original  # type: ignore[assignment]
    return recorder


def matches_accepted(submitted: list[dict], accepted: list[dict]) -> bool:
    """A case passes if the submitted list equals any accepted list, field for field."""
    def normalise(actions: list[dict]) -> list[dict]:
        out = []
        for action in actions:
            flat = {k: v for k, v in action.items() if k != "new_patient"}
            flat.update(action.get("new_patient") or {})
            out.append({k: str(v) for k, v in sorted(flat.items()) if v is not None})
        return out

    mine = normalise(submitted)
    return any(mine == normalise(option.get("actions") or []) for option in accepted)


# ---------------------------------------------------------------- reporting


def summarise(recorder: Recorder) -> dict[str, Any]:
    prompts = [c["prompt_chars"] for c in recorder.llm_calls]
    seconds = [c["seconds"] for c in recorder.llm_calls]
    return {
        "llm_calls": len(recorder.llm_calls),
        "prompt_chars_first": prompts[0] if prompts else 0,
        "prompt_chars_last": prompts[-1] if prompts else 0,
        "prompt_chars_max": max(prompts) if prompts else 0,
        "llm_seconds_total": round(sum(seconds), 1),
        "agent_turns": sum(1 for s, _ in recorder.transcript if s == "Agent"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("problems", nargs="*", help="problem ids; default is every cached problem")
    parser.add_argument("--refresh", type=Path, metavar="COOKIES", help="re-pull cases using this cookie jar")
    parser.add_argument("--case", help="run one case id only")
    parser.add_argument("--json", type=Path, help="write the full report here")
    parser.add_argument("--transcript", action="store_true", help="print each call")
    args = parser.parse_args()

    load_environment()

    if args.refresh:
        CASES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CASES_CACHE.write_text(
            json.dumps(fetch_cases(args.refresh), indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(f"cached {CASES_CACHE}")

    problems = load_cases()
    wanted = args.problems or list(problems)
    report: list[dict[str, Any]] = []

    for problem_id in wanted:
        problem = problems.get(problem_id)
        if problem is None:
            print(f"unknown problem {problem_id}")
            continue
        for case in problem["examples"]:
            if args.case and case["case_id"] != args.case:
                continue
            recorder = Recorder()
            started = time.monotonic()
            await run_case(case, recorder)
            passed = matches_accepted(recorder.actions, case["accepted"])
            stats = summarise(recorder)
            row = {
                "problem": problem_id,
                "case_id": case["case_id"],
                "caller": case["caller"],
                "passed": passed,
                "seconds": round(time.monotonic() - started, 1),
                "submitted": recorder.actions,
                "accepted": case["accepted"],
                "error": recorder.error,
                **stats,
            }
            report.append(row)
            mark = "PASS" if passed else "FAIL"
            print(
                f"{mark} {problem_id:18} {case['caller'][:26]:26} "
                f"{row['seconds']:6.1f}s  llm={stats['llm_calls']:2}  "
                f"prompt {stats['prompt_chars_first']//1000}k->{stats['prompt_chars_max']//1000}k  "
                f"{recorder.error or ''}"
            )
            if not passed:
                print(f"     submitted: {json.dumps(recorder.actions, ensure_ascii=False)}")
            if args.transcript:
                for speaker, text in recorder.transcript:
                    print(f"     {speaker:8} {text}")

    passed = sum(1 for row in report if row["passed"])
    print(f"\n{passed}/{len(report)} cases passed")
    if args.json:
        args.json.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"report written to {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
