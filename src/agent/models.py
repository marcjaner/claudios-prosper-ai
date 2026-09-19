"""Models exchanged between the booking agent and the language model."""

from __future__ import annotations

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

