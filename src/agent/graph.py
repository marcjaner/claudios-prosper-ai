"""The stage graph that configures the agent: what it may say and do, and when."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from .tools import CLINIC_TOOL_NAMES

GRAPH_PATH = Path(
    os.getenv("AGENT_GRAPH_PATH", Path(__file__).resolve().parents[2] / "graphs" / "default.json")
)

RECORD_FACTS_TOOL = "record_facts"
GO_TO_TOOL = "go_to"
# The runtime injects these into every stage, so a stage may not also list them.
GRAPH_TOOL_NAMES = frozenset({RECORD_FACTS_TOOL, GO_TO_TOOL})


class InvalidGraph(ValueError):
    """A graph that would break a call if it were ever loaded."""


class Position(BaseModel):
    x: float = 0
    y: float = 0


class Node(BaseModel):
    id: str
    prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    clears: list[str] = Field(default_factory=list)
    position: Position = Field(default_factory=Position)


class Edge(BaseModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    requires: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class Graph(BaseModel):
    """One agent configuration: how it speaks, and the stages it speaks through.

    The system prompt lives here rather than beside the code so the builder owns
    the whole configuration. Two copies of it, one editable and one not, is how
    the graph and the prompt came to describe different agents.
    """

    entry: str
    system: str = ""
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> Graph:
        ids = [node.id for node in self.nodes]
        duplicates = {node_id for node_id in ids if ids.count(node_id) > 1}
        if duplicates:
            raise InvalidGraph(f"Duplicate stage ids: {sorted(duplicates)}")
        known = set(ids)
        if self.entry not in known:
            raise InvalidGraph(f"Entry stage {self.entry!r} does not exist")
        for edge in self.edges:
            for end in (edge.source, edge.target):
                if end not in known:
                    raise InvalidGraph(f"Transition points at unknown stage {end!r}")
        for node in self.nodes:
            unknown = sorted(set(node.tools) - set(CLINIC_TOOL_NAMES))
            if unknown:
                raise InvalidGraph(f"Stage {node.id!r} lists unknown tools: {unknown}")
        return self

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise InvalidGraph(f"Unknown stage {node_id!r}")

    def eligible_targets(self, node_id: str, facts: dict[str, str]) -> list[str]:
        """Stages reachable from here given what the call currently knows."""
        return [
            edge.target
            for edge in self.edges
            if edge.source == node_id and all(key in facts for key in edge.requires)
        ]

    def missing_for(self, node_id: str, target: str, facts: dict[str, str]) -> list[str]:
        """Fact keys that block this transition, for the trace."""
        missing: list[str] = []
        for edge in self.edges:
            if edge.source == node_id and edge.target == target:
                missing = [key for key in edge.requires if key not in facts]
                break
        return missing


def parse_graph(data: Any) -> Graph:
    """Build a graph, reporting shape and reference problems the same way."""
    try:
        return Graph.model_validate(data)
    except ValidationError as exc:
        raise InvalidGraph(_first_message(exc)) from exc


def _first_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    return str(error.get("ctx", {}).get("error") or error.get("msg", "invalid graph"))


def load_graph(path: Path = GRAPH_PATH) -> Graph:
    return parse_graph(json.loads(path.read_text(encoding="utf-8")))


def save_graph(graph: Graph, path: Path = GRAPH_PATH) -> None:
    """Validate, then replace atomically: a half-written graph must never load."""
    payload = parse_graph(graph.model_dump(by_alias=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(_dump(payload), encoding="utf-8")
    temporary.replace(path)


def _dump(graph: Graph) -> str:
    data: dict[str, Any] = graph.model_dump(by_alias=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
