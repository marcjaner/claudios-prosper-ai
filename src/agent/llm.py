"""OpenAI-compatible completion client using the bare ``openai`` SDK."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ValidationError

from agent.utils import load_environment

_logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_HELMCODE_BASE_URL = "https://api.helmcode.com/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _environment_config() -> tuple[str | None, str, str, str | None]:
    load_environment()
    helmcode_api_key = os.getenv("HELMCODE_API_KEY")
    if helmcode_api_key:
        return (
            helmcode_api_key,
            os.getenv("HELMCODE_BASE_URL") or DEFAULT_HELMCODE_BASE_URL,
            os.getenv("HELMCODE_MODEL") or DEFAULT_MODEL,
            None,
        )

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        return (
            None,
            os.getenv("HELMCODE_BASE_URL") or DEFAULT_HELMCODE_BASE_URL,
            os.getenv("HELMCODE_MODEL") or DEFAULT_MODEL,
            None,
        )

    openai_model = os.getenv("OPENAI_MODEL")
    if not openai_model:
        raise RuntimeError("Set OPENAI_MODEL in .env before calling OpenAI.")
    return (
        openai_api_key,
        os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL,
        openai_model or DEFAULT_MODEL,
        os.getenv("OPENAI_REASONING_EFFORT") or None,
    )


@dataclass(frozen=True)
class Completion:
    text: str
    sources: list[str]
    usage: Usage


@dataclass(frozen=True)
class StructuredCompletion[T: BaseModel]:
    data: T
    usage: Usage


@dataclass(frozen=True)
class LLMToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCompletion:
    text: str
    tool_calls: list[LLMToolCall]
    usage: Usage


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


class LLMResponseError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        default_model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_temperature: float = 0.0,
        default_reasoning_effort: str | None = None,
        default_max_tokens: int = 4096,
        max_retries: int = 2,
    ) -> None:
        """Configure completion defaults and lazy client caches."""
        self.default_model = default_model
        self._api_key = api_key
        self._base_url = base_url
        self.default_temperature = default_temperature
        self.default_reasoning_effort = default_reasoning_effort
        self.default_max_tokens = default_max_tokens
        self.max_retries = max_retries
        self._clients: dict[tuple[str, str | None], OpenAI] = {}

    def _get_client(self, model_id: str) -> OpenAI:
        cache_key = (model_id, self._base_url)
        cached = self._clients.get(cache_key)
        if cached is not None:
            _logger.debug("Reusing cached LLM client for model=%s", model_id)
            return cached

        environment_api_key, environment_base_url, _, _ = _environment_config()
        api_key = self._api_key or environment_api_key
        if not api_key or api_key == "your-helmcode-api-key":
            raise RuntimeError(
                "Set HELMCODE_API_KEY or OPENAI_API_KEY in .env before calling the LLM."
            )

        base_url = self._base_url or environment_base_url
        _logger.info("Creating LLM client model=%s base_url=%s", model_id, base_url)
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=self.max_retries,
        )
        self._clients[cache_key] = client
        return client

    @staticmethod
    def _usage_from(response_usage: object) -> Usage:
        """
        Extract a :class:`Usage` from a raw ``ChatCompletion.usage``.

        All attribute reads are guarded: ``usage``, the ``_details`` subobjects,
        and the OpenRouter-only ``cost_details`` (untyped in the SDK) are all
        optional. Returns an all-zero :class:`Usage` when nothing is present.
        """
        if response_usage is None:
            return Usage()

        def _get(obj: object, name: str) -> int:
            value = getattr(obj, name, None)
            return int(value) if value is not None else 0

        prompt = _get(response_usage, "prompt_tokens")
        completion = _get(response_usage, "completion_tokens")
        total = _get(response_usage, "total_tokens")

        completion_details = getattr(response_usage, "completion_tokens_details", None)
        reasoning = (
            _get(completion_details, "reasoning_tokens") if completion_details else 0
        )

        prompt_details = getattr(response_usage, "prompt_tokens_details", None)
        cached = _get(prompt_details, "cached_tokens") if prompt_details else 0

        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            reasoning_tokens=reasoning,
            cached_tokens=cached,
        )

    @staticmethod
    def _sources_from(message: object) -> list[str]:
        """
        Scrape ``url_citation`` URLs off ``message.annotations``.

        OpenRouter returns citations as typed ``Annotation`` objects with
        ``type == "url_citation"`` and a nested ``url_citation.url``. Order is
        preserved; duplicates are dropped.
        """
        annotations = getattr(message, "annotations", None) or []
        sources: list[str] = []
        for annotation in annotations:
            if getattr(annotation, "type", None) != "url_citation":
                continue
            citation = getattr(annotation, "url_citation", None)
            url = getattr(citation, "url", None) if citation is not None else None
            if isinstance(url, str):
                sources.append(url)
        return list(dict.fromkeys(sources))

    def _common_create_kwargs(
        self,
        prompt: str,
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        reasoning_effort: str | None,
    ) -> dict[str, object]:
        """Core kwargs shared by both completion methods."""
        kwargs: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            # SDK deprecates ``max_tokens`` in favour of ``max_completion_tokens``.
            "max_completion_tokens": (
                self.default_max_tokens if max_tokens is None else max_tokens
            ),
        }
        resolved_reasoning_effort = reasoning_effort or self.default_reasoning_effort
        if resolved_reasoning_effort:
            kwargs["reasoning_effort"] = resolved_reasoning_effort
        else:
            kwargs["temperature"] = (
                self.default_temperature if temperature is None else temperature
            )
        return kwargs

    def _create_chat_completion(
        self,
        client: OpenAI,
        kwargs: dict[str, object],
        *,
        model: str,
    ) -> ChatCompletion:
        """
        Retry chat completions whose HTTP response contains malformed JSON.

        The SDK retries transport and HTTP failures, but not response parsing
        failures, so apply the configured retry budget at this boundary too.
        """
        for attempt in range(self.max_retries + 1):
            try:
                _logger.debug(
                    "Sending completion request model=%s attempt=%d", model, attempt + 1
                )
                return client.chat.completions.create(**cast(Any, kwargs))
            except json.JSONDecodeError as exc:
                if attempt >= self.max_retries:
                    raise LLMResponseError(
                        "Provider returned malformed JSON "
                        f"for model {model!r} after {attempt + 1} attempts."
                    ) from exc

                delay_seconds = min(0.5 * (2**attempt), 2.0)
                _logger.warning(
                    "Provider returned malformed JSON for model %r; "
                    "retrying in %.1fs (%d/%d).",
                    model,
                    delay_seconds,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(delay_seconds)

        raise AssertionError("completion retry loop exhausted unexpectedly")

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra_body: dict[str, object] | None = None,
    ) -> Completion:
        """Run a plain completion and return its text and verified citation URLs."""
        client = self._get_client(model or self.default_model)
        resolved_model = model or self.default_model
        # Build kwargs and splat: the dict shape is dynamic (tools/extra_body are
        # optional), so we pass it as Any — the SDK validates at runtime.
        kwargs: Any = self._common_create_kwargs(
            prompt, resolved_model, temperature, max_tokens, reasoning_effort
        )
        if extra_body:
            kwargs["extra_body"] = extra_body
        response = self._create_chat_completion(client, kwargs, model=resolved_model)
        _logger.info("Received plain completion model=%s", resolved_model)
        message = response.choices[0].message
        text = message.content or ""
        return Completion(
            text=text,
            sources=self._sources_from(message),
            usage=self._usage_from(response.usage),
        )

    def complete_structured[T: BaseModel](
        self,
        prompt: str,
        schema: type[T],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra_body: dict[str, object] | None = None,
    ) -> StructuredCompletion[T]:
        """Run a completion parsed into a Pydantic schema via ``json_schema``."""
        client = self._get_client(model or self.default_model)
        resolved_model = model or self.default_model
        # Same dynamic-shape splat as complete(); see the note there.
        kwargs: Any = self._common_create_kwargs(
            prompt, resolved_model, temperature, max_tokens, reasoning_effort
        )
        if extra_body:
            kwargs["extra_body"] = extra_body
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": False,
            },
        }

        response = self._create_chat_completion(client, kwargs, model=resolved_model)
        _logger.info(
            "Received structured completion model=%s schema=%s",
            resolved_model,
            schema.__name__,
        )
        content = response.choices[0].message.content or ""
        try:
            data = schema.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise LLMResponseError(
                f"Model response did not validate against {schema.__name__}."
            ) from exc

        return StructuredCompletion(data=data, usage=self._usage_from(response.usage))

    def complete_with_tools(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> ToolCompletion:
        """Run a completion and return native function calls from the message."""
        resolved_model = model or self.default_model
        client = self._get_client(resolved_model)
        kwargs: Any = self._common_create_kwargs(
            prompt, resolved_model, temperature, max_tokens, reasoning_effort
        )
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
        response = self._create_chat_completion(client, kwargs, model=resolved_model)
        message = response.choices[0].message
        tool_calls = []
        for call in message.tool_calls or []:
            if call.type != "function":
                continue
            arguments = json.loads(call.function.arguments)
            if not isinstance(arguments, dict):
                raise TypeError(
                    f"Tool arguments for {call.function.name} must be an object."
                )
            tool_calls.append(
                LLMToolCall(
                    call_id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                )
            )
        return ToolCompletion(
            text=message.content or "",
            tool_calls=tool_calls,
            usage=self._usage_from(response.usage),
        )


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """Return the shared default client for the process."""
    api_key, base_url, model, reasoning_effort = _environment_config()
    return LLMClient(
        model,
        api_key=api_key,
        base_url=base_url,
        default_reasoning_effort=reasoning_effort,
    )
