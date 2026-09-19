"""Call the configured OpenAI-compatible chat model."""

from __future__ import annotations

from pydantic import BaseModel

from agent.llm import get_llm_client


class MathAnswer(BaseModel):
    answer: int
    explanation: str


if __name__ == "__main__":
    client = get_llm_client()

    result = client.complete("What is 2+2?")
    print(result.text)

    structured_result = client.complete_structured(
        "What is 2+2? Return the answer and a short explanation.",
        MathAnswer,
    )
    print(structured_result.data)
