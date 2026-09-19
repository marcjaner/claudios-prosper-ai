from dataclasses import dataclass, field
from typing import Any

from pipecat.frames.frames import SystemFrame


@dataclass
class LLMRequestStartedFrame(SystemFrame):
    request_id: str
    model: str


@dataclass
class LLMResponseFinishedFrame(SystemFrame):
    request_id: str
    model: str
    duration_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int
    cached_tokens: int


@dataclass
class LLMRequestFailedFrame(SystemFrame):
    request_id: str
    model: str
    duration_ms: float
    error_type: str
    error_message: str


@dataclass
class ToolCallStartedFrame(SystemFrame):
    tool: str
    tool_call_id: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallFinishedFrame(SystemFrame):
    tool: str
    tool_call_id: str
    duration_ms: float
    status: str
    result_summary: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class TTSRequestedFrame(SystemFrame):
    text: str
