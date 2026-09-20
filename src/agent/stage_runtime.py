"""Per-call graph state: what the agent knows, where it is, and what it may do."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph import GO_TO_TOOL, RECORD_FACTS_TOOL, Graph, load_graph
from .tools import OUTCOME_TOOL_NAMES, SUBMISSION_TOOL_NAMES

# An action step's speech is written before its own results, so the last step of a
# turn is always tool-free: otherwise a booking on the final step is never confirmed.
MAX_ACTION_STEPS = 4

GRAPH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": RECORD_FACTS_TOOL,
            "description": (
                "Record what you have learned about this call so later stages can use it. "
                "Keys and values are plain strings. Never announce this to the caller."
            ),
            "parameters": {
                "type": "object",
                "properties": {"facts": {"type": "object"}},
                "required": ["facts"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": GO_TO_TOOL,
            "description": (
                "Move to another stage of the call. Only allowed once that stage's required "
                "facts are recorded. Never announce this to the caller."
            ),
            "parameters": {
                "type": "object",
                "properties": {"stage": {"type": "string"}},
                "required": ["stage"],
                "additionalProperties": False,
            },
        },
    },
]


class InvalidFacts(ValueError):
    """A record_facts payload the runtime will not apply."""


@dataclass
class Operation:
    """One requested tool call and what became of it — the unit of the trace."""

    name: str
    arguments: dict[str, Any]
    status: str  # executed | refused | failed
    result: Any = None
    detail: str = ""


@dataclass
class HistoryEntry:
    speaker: str  # caller | agent
    text: str = ""
    operations: list[Operation] = field(default_factory=list)


@dataclass
class CallGraph:
    """The graph a call is running, frozen at call start, plus where that call is."""

    graph: Graph
    stage_id: str
    facts: dict[str, str] = field(default_factory=dict)
    history: list[HistoryEntry] = field(default_factory=list)
    turn: int = 0
    failed_tools: set[str] = field(default_factory=set)
    failed_calls: set[str] = field(default_factory=set)
    calls_made: set[str] = field(default_factory=set)
    # A record belongs to the call, not to the turn that wrote it.
    submitted: list[str] = field(default_factory=list)
    submitted_calls: set[str] = field(default_factory=set)
    pending_submission: bool = False

    def start_turn(self) -> None:
        self.turn += 1
        self.failed_tools.intersection_update(SUBMISSION_TOOL_NAMES)
        self.calls_made.clear()

    def record_submission(self, name: str, arguments: dict[str, Any]) -> None:
        self.submitted.append(name)
        self.submitted_calls.add(self.signature(name, arguments))

    @staticmethod
    def signature(name: str, arguments: dict[str, Any]) -> str:
        return f"{name}:{sorted(arguments.items(), key=lambda item: item[0])}"

    @classmethod
    def start(cls, graph: Graph | None = None) -> CallGraph:
        resolved = graph or load_graph()
        return cls(graph=resolved, stage_id=resolved.entry)

    @property
    def stage(self):
        return self.graph.node(self.stage_id)

    def allowed_tools(self) -> list[str]:
        if self.terminal_outcome:
            return []
        return list(self.stage.tools)

    @property
    def terminal_outcome(self) -> str | None:
        return next((name for name in self.submitted if name in OUTCOME_TOOL_NAMES), None)

    def has_exits(self) -> bool:
        return any(edge.source == self.stage_id for edge in self.graph.edges)

    def refuse_reason(self, name: str, arguments: dict[str, Any]) -> str:
        """Why this call is not allowed here, or an empty string if it is."""
        if self.terminal_outcome:
            return "A final outcome is already recorded; explain it without starting another action."
        if name == RECORD_FACTS_TOOL:
            return "" if self.has_facts_payload(arguments) else "record_facts needs a flat mapping of strings"
        if name == GO_TO_TOOL:
            return self._refuse_transition(str(arguments.get("stage", "")))
        if name not in self.stage.tools:
            return f"{name} is not available in stage {self.stage_id}"
        if name in SUBMISSION_TOOL_NAMES:
            if self.pending_submission:
                return "A submission is still in flight; wait for its result before another action."
            if self.failed_tools.intersection(SUBMISSION_TOOL_NAMES):
                return "A previous submission may already have been received; do not submit another action."
            if self.signature(name, arguments) in self.failed_calls:
                return f"{name} already failed with these arguments in this call"
            refusal = self._refuse_second_record(name, arguments)
            if refusal:
                return refusal
        if self.signature(name, arguments) in self.calls_made:
            # The answer is already in the conversation against a read-only EHR,
            # so repeating it would spend the turn's budget saying nothing new.
            # Only calls that succeeded are recorded, so a failed read may retry.
            return f"{name} was already called with these arguments this turn"
        return ""

    def _refuse_second_record(self, name: str, arguments: dict[str, Any]) -> str:
        """Guard the record itself, which outlives the turn that wrote it.

        A case accepts an exact list of actions, so a spurious extra fails one
        the agent had already got right — a correct booking followed by an
        escalation because the caller asked something unrelated is a real
        failure we have seen. Two *writes* stay legal: a caller may cancel two
        appointments in one call.
        """
        if self.signature(name, arguments) in self.submitted_calls:
            return f"{name} with these exact values was already reported on this call"
        if not self.submitted:
            return ""
        if OUTCOME_TOOL_NAMES.intersection(self.submitted):
            return f"this call already ended with {self.submitted[-1]}; it reports one ending"
        if name in OUTCOME_TOOL_NAMES:
            return (
                f"{name} would contradict the {self.submitted[-1]} already reported; "
                "not knowing the answer to a question is not an outcome"
            )
        return ""

    def _refuse_transition(self, target: str) -> str:
        known = {node.id for node in self.graph.nodes}
        if target not in known:
            return f"no stage named {target}"
        missing = self.graph.missing_for(self.stage_id, target, self.facts)
        if target not in self.graph.eligible_targets(self.stage_id, self.facts):
            if missing:
                return f"{target} needs {', '.join(missing)} first"
            return f"no transition from {self.stage_id} to {target}"
        return ""

    @staticmethod
    def has_facts_payload(arguments: dict[str, Any]) -> bool:
        facts = arguments.get("facts")
        return isinstance(facts, dict) and all(
            isinstance(key, str) and isinstance(value, (str, int, float, bool))
            for key, value in facts.items()
        )

    def record_facts(self, arguments: dict[str, Any]) -> dict[str, str]:
        if not self.has_facts_payload(arguments):
            raise InvalidFacts("facts must be a flat mapping of strings")
        written = {key: str(value) for key, value in arguments["facts"].items()}
        self.facts.update(written)
        return written

    def enter(self, target: str) -> list[str]:
        """Move to a stage and drop the facts it resets. Returns the cleared keys."""
        node = self.graph.node(target)
        cleared = [key for key in node.clears if key in self.facts]
        for key in cleared:
            self.facts.pop(key)
        self.stage_id = target
        return cleared

    def transition_options(self) -> list[dict[str, Any]]:
        """Every exit from here with what it still needs, for the prompt and the UI."""
        return [
            {
                "stage": edge.target,
                "requires": list(edge.requires),
                "missing": [key for key in edge.requires if key not in self.facts],
            }
            for edge in self.graph.edges
            if edge.source == self.stage_id
        ]

    def ready_fact_transition(self) -> str | None:
        """Return the sole fact-gated transition that is ready, if there is one."""
        ready = [
            edge.target
            for edge in self.graph.edges
            if edge.source == self.stage_id
            and edge.requires
            and all(key in self.facts for key in edge.requires)
        ]
        return ready[0] if len(ready) == 1 else None


def render_context(state: CallGraph) -> str:
    """Everything the model needs about where the call is and what it knows."""
    sections = [f"Current stage: {state.stage_id}"]
    if state.terminal_outcome:
        sections.append(
            f"Final outcome already recorded: {state.terminal_outcome}. "
            "Explain the recorded outcome briefly. Do not offer another appointment, "
            "promise another action, or say that the phone call or system is closed. "
            "If the caller says goodbye, say goodbye without repeating the explanation."
        )
    elif state.stage.prompt.strip():
        sections.append(state.stage.prompt.strip())
    sections.append(_render_facts(state.facts))
    options = state.transition_options()
    if options and not state.terminal_outcome:
        sections.append(_render_transitions(options))
    sections.append(_render_history(state.history))
    return "\n\n".join(sections)


def _render_facts(facts: dict[str, str]) -> str:
    if not facts:
        return "Recorded facts:\n  (nothing recorded yet)"
    lines = "\n".join(f"  {key} = {value}" for key, value in sorted(facts.items()))
    return f"Recorded facts:\n{lines}"


def _render_transitions(options: list[dict[str, Any]]) -> str:
    lines = []
    for option in options:
        needs = ", ".join(option["requires"]) or "nothing"
        state = "ready" if not option["missing"] else f"still missing {', '.join(option['missing'])}"
        lines.append(f"  {option['stage']} — requires {needs} ({state})")
    return (
        "Stages you can move to with go_to, and the exact fact keys each one needs:\n"
        + "\n".join(lines)
    )


def _render_history(history: list[HistoryEntry]) -> str:
    if not history:
        return "Conversation so far:\n  (this is the first thing said)"
    lines = []
    catalogues: list[dict[str, Any]] = []
    for entry in history:
        if entry.text:
            lines.append(f"  {entry.speaker}: {entry.text}")
        for operation in entry.operations:
            if (
                operation.name == "get_clinic_catalogue"
                and operation.status == "executed"
                and isinstance(operation.result, dict)
            ):
                if operation.result in catalogues:
                    lines.append(
                        "  [tool] get_clinic_catalogue({}) -> same result as "
                        f"catalogue #{catalogues.index(operation.result) + 1} above."
                    )
                    continue
                catalogues.append(operation.result)
                lines.append(
                    f"  {_render_operation(operation)} [catalogue #{len(catalogues)}]"
                )
                continue
            lines.append(f"  {_render_operation(operation)}")
    return "Conversation so far:\n" + "\n".join(lines)


def _render_operation(operation: Operation) -> str:
    call = f"{operation.name}({operation.arguments})"
    if operation.status == "executed":
        return f"[tool] {call} -> {operation.result}"
    return f"[tool] {call} -> {operation.status}: {operation.detail}"
