"""Call a chat model through Helmcode's OpenAI-compatible API."""

from __future__ import annotations

import os

from pydantic import BaseModel

from agent.llm import LLMClient


class MathAnswer(BaseModel):
    answer: int
    explanation: str


if __name__ == "__main__":
    client = LLMClient(os.getenv("HELMCODE_MODEL", "deepseek-v4-flash"))

    result = client.complete("What is 2+2?")
    print(result.text)

    structured_result = client.complete_structured(
        "What is 2+2? Return the answer and a short explanation.",
        MathAnswer,
    )
    print(structured_result.data)
