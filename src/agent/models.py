"""Models exchanged between the booking agent and the language model."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    """The model's immediate reply and any tools it wants to run."""

    immediate_answer: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolResult(BaseModel):
    name: str
    output: Any


class Tool(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any]
    execute: Callable[..., Any]

    model_config = {"arbitrary_types_allowed": True}

    @property
    def definition(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters,
        }}
