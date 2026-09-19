"""OpenRouter-compatible completion client (bare ``openai`` SDK)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any, Generic, TypeVar, cast

from openai import OpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ValidationError

from agent.utils import load_environment

T = TypeVar("T", bound=BaseModel)

_logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-v4-flash"

@dataclass(frozen=True)
class Completion:
    text: str
    sources: list[str]
    usage: Usage

@dataclass(frozen=True)
class StructuredCompletion(Generic[T]):
    data: T
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


class LLMClient:
    def __init__(
        self,
        default_model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_temperature: float = 0.0,
        default_max_tokens: int = 4096,
        max_retries: int = 2,
    ) -> None:
        """Configure completion defaults and lazy client caches."""
        self.default_model = default_model
        self._api_key = api_key
        self._base_url = base_url
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self.max_retries = max_retries
        self._clients: dict[tuple[str, str | None], OpenAI] = {}

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def _get_client(self, model_id: str) -> OpenAI:
        cache_key = (model_id, self._base_url)
        cached = self._clients.get(cache_key)
        if cached is not None:
            _logger.debug("Reusing cached LLM client for model=%s", model_id)
            return cached

        load_environment()
        api_key = self._api_key or os.getenv("HELMCODE_API_KEY")
        if not api_key or api_key == "your-helmcode-api-key":
            raise RuntimeError("Set HELMCODE_API_KEY in .env before calling Helmcode.")

        base_url = self._base_url or os.getenv(
            "HELMCODE_BASE_URL", "https://api.helmcode.com/v1"
        )
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
    ) -> dict[str, object]:
        """Core kwargs shared by both completion methods."""
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": (
                self.default_temperature if temperature is None else temperature
            ),
            # SDK deprecates ``max_tokens`` in favour of ``max_completion_tokens``.
            "max_completion_tokens": (
                self.default_max_tokens if max_tokens is None else max_tokens
            ),
        }

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
                _logger.debug("Sending completion request model=%s attempt=%d", model, attempt + 1)
                return client.chat.completions.create(**cast(Any, kwargs))
            except json.JSONDecodeError as exc:
                if attempt >= self.max_retries:
                    raise Exception(
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
        extra_body: dict[str, object] | None = None,
    ) -> Completion:
        """Run a plain completion and return its text and verified citation URLs."""
        client = self._get_client(model or self.default_model)
        resolved_model = model or self.default_model
        # Build kwargs and splat: the dict shape is dynamic (tools/extra_body are
        # optional), so we pass it as Any — the SDK validates at runtime.
        kwargs: Any = self._common_create_kwargs(
            prompt, resolved_model, temperature, max_tokens
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

    def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, object] | None = None,
    ) -> StructuredCompletion[T]:
        """Run a completion parsed into a Pydantic schema via ``json_schema``."""
        client = self._get_client(model or self.default_model)
        resolved_model = model or self.default_model
        # Same dynamic-shape splat as complete(); see the note there.
        kwargs: Any = self._common_create_kwargs(
            prompt, resolved_model, temperature, max_tokens
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
        _logger.info("Received structured completion model=%s schema=%s", resolved_model, schema.__name__)
        content = response.choices[0].message.content or ""
        try:
            data = schema.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise Exception(
                f"Model response did not validate against {schema.__name__}."
            ) from exc

        return StructuredCompletion(data=data, usage=self._usage_from(response.usage))
