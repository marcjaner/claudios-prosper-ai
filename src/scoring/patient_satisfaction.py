import asyncio
import time
from collections.abc import Callable

import httpx
from loguru import logger

from observability import CallUpdate, Event, emit

from .jev import (
    DEFAULT_MODEL,
    JEV_URL,
    REQUEST_TIMEOUT_SECONDS,
    SPOKEN_KINDS,
)

MIN_SECONDS_BETWEEN_SAMPLES = 2
ROLLING_TURN_COUNT = 8
MIN_CONFIDENCE = 0.45
TREND_CHANGE_POINTS = 8

DIMENSIONS = {
    "comfort": {
        "type": "score",
        "instructions": "Evaluate whether the patient currently appears likely to feel heard, respected, and at ease. Use patient signals and the assistant's latest response, but do not invent a positive patient reaction that has not occurred.",
        "criteria": [
            "The patient shows strong distress, impatience, distrust, or frustration.",
            "The patient appears uncomfortable or frustrated and the concern is not resolving.",
            "The patient's comfort is unclear or mixed.",
            "The patient appears mostly comfortable, with only minor hesitation or friction.",
            "The patient clearly appears comfortable, heard, and respected.",
        ],
    },
    "clarity": {
        "type": "score",
        "instructions": "Evaluate whether the conversation and current next step are likely to be clear to the patient, using both speakers while avoiding invented patient reactions.",
        "criteria": [
            "The patient clearly does not understand what is happening or what comes next.",
            "The patient shows substantial confusion or repeatedly seeks clarification.",
            "The patient's understanding is unclear or mixed.",
            "The patient appears to understand with only minor uncertainty.",
            "The patient clearly understands and acknowledges the next step.",
        ],
    },
    "ease": {
        "type": "score",
        "instructions": "Evaluate the amount of friction the patient experiences, including repetition, corrections, waiting, or difficulty progressing.",
        "criteria": [
            "The patient encounters severe friction, repeated failures, or must correct the agent multiple times.",
            "The patient encounters clear and unresolved friction or repetition.",
            "The amount of friction is unclear or ordinary for the task.",
            "The interaction appears mostly easy, with only minor friction.",
            "The interaction progresses smoothly without observable patient friction.",
        ],
    },
    "next_step_confidence": {
        "type": "score",
        "instructions": "Evaluate whether the patient appears confident in the proposed action or next step, without assuming satisfaction from the agent's words alone.",
        "criteria": [
            "The patient rejects or strongly distrusts the proposed action or next step.",
            "The patient shows meaningful doubt or reluctance about what will happen next.",
            "The patient's confidence is unclear or mixed.",
            "The patient appears mostly confident, with only minor uncertainty.",
            "The patient clearly accepts and trusts the action or next step.",
        ],
    },
}

QUESTIONS = {
    **DIMENSIONS,
    "sufficient_signal": {
        "type": "noul",
        "instructions": "Is there enough conversational evidence to estimate the patient's current experience? Return a low probability for greetings, brief identity answers, or scheduling facts without a meaningful response from the other speaker.",
    },
}

REASONS = {
    "comfort": "Patient comfort is the weakest signal.",
    "clarity": "The patient may not fully understand the current next step.",
    "ease": "Repetition or difficulty progressing may be creating friction.",
    "next_step_confidence": "The patient appears uncertain about what happens next.",
}


def satisfaction_state(score: float, confidence: float) -> str:
    if confidence < MIN_CONFIDENCE:
        return "insufficient_signal"
    if score >= 70:
        return "satisfied"
    if score >= 40:
        return "neutral"
    return "frustrated"


def satisfaction_trend(score: float, previous: dict | None) -> str:
    if previous is None or previous["state"] == "insufficient_signal":
        return "stable"
    change = score - previous["score"]
    if change >= TREND_CHANGE_POINTS:
        return "improving"
    if change <= -TREND_CHANGE_POINTS:
        return "declining"
    return "stable"


def _score_answer(answers: dict, key: str) -> float:
    answer = answers.get(key)
    score = answer.get("score") if isinstance(answer, dict) else None
    if (
        not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not 0 <= score <= 4
    ):
        raise ValueError(f"jev answer for '{key}' has no score in 0..4")
    return float(score)


async def evaluate_patient_satisfaction(
    turns: list[dict],
    previous: dict | None,
    client: httpx.AsyncClient,
    api_key: str,
    model: str = DEFAULT_MODEL,
    *,
    elapsed_seconds: float = 0,
    final: bool = False,
) -> dict:
    payload = {
        "model": model,
        "state": {
            "turns": turns[-ROLLING_TURN_COUNT:],
            "call_elapsed_seconds": round(max(0, elapsed_seconds), 1),
            "previous_measurement": previous,
            "final": final,
        },
        "questions": QUESTIONS,
    }
    response = await client.post(
        JEV_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    body = response.json()
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("jev response has no answers object")  # noqa: TRY004

    dimensions = {key: _score_answer(answers, key) for key in DIMENSIONS}
    signal = answers.get("sufficient_signal")
    confidence = signal.get("noul") if isinstance(signal, dict) else None
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise ValueError(
            "jev answer for 'sufficient_signal' has no probability in 0..1"
        )

    score = round(sum(dimensions.values()) / (len(dimensions) * 4) * 100, 1)
    state = satisfaction_state(score, float(confidence))
    weakest = min(dimensions, key=lambda key: dimensions[key])
    if state == "insufficient_signal":
        reason = "There is not enough patient language to estimate satisfaction yet."
    elif state == "satisfied":
        reason = "The patient appears comfortable with the conversation and next step."
    else:
        reason = REASONS[weakest]
    return {
        "score": score,
        "state": state,
        "confidence": round(float(confidence), 2),
        "trend": satisfaction_trend(score, previous),
        "reason": reason,
        "dimensions": dimensions,
        "model": body.get("model", model),
        "evaluator": "jev-patient-satisfaction-v1",
        "scored_at": time.time(),
        "final": final,
    }


def _spoken_turn(event: Event) -> dict | None:
    speaker = SPOKEN_KINDS.get(event.kind)
    text = event.payload.get("text")
    if speaker is None or not isinstance(text, str) or not text.strip():
        return None
    return {"speaker": speaker, "text": text.strip(), "ts": event.ts}


async def run_patient_satisfaction_worker(
    queue: asyncio.Queue,
    api_key: str,
    model: str = DEFAULT_MODEL,
    publish: Callable[[str, str, dict], None] = emit,
) -> None:
    conversations: dict[str, list[dict]] = {}
    call_fields: dict[str, dict] = {}
    previous: dict[str, dict] = {}
    last_sample_at: dict[str, float] = {}
    last_turn_count: dict[str, int] = {}
    sample_indexes: dict[str, int] = {}
    requested: dict[str, bool] = {}
    tasks: dict[str, asyncio.Task] = {}

    async def sample(call_id: str, client: httpx.AsyncClient) -> None:
        try:
            while call_id in requested:
                final = requested.pop(call_id)
                final = (
                    final or call_fields.get(call_id, {}).get("ended_at") is not None
                )
                turns = conversations.get(call_id, [])
                turn_count = len(turns)
                if not turn_count or turn_count <= last_turn_count.get(call_id, 0):
                    continue
                sample_at = turns[-1]["ts"]
                if (
                    not final
                    and call_id in last_sample_at
                    and sample_at - last_sample_at[call_id]
                    < MIN_SECONDS_BETWEEN_SAMPLES
                ):
                    continue

                snapshot = [
                    {"speaker": turn["speaker"], "text": turn["text"]}
                    for turn in turns[-ROLLING_TURN_COUNT:]
                ]
                started_at = call_fields.get(call_id, {}).get(
                    "started_at", turns[0]["ts"]
                )
                result = await evaluate_patient_satisfaction(
                    snapshot,
                    previous.get(call_id),
                    client,
                    api_key,
                    model,
                    elapsed_seconds=sample_at - started_at,
                    final=final,
                )
                result["final"] = (
                    final or call_fields.get(call_id, {}).get("ended_at") is not None
                )
                sample_index = sample_indexes.get(call_id, 0) + 1
                result["sample_index"] = sample_index
                publish(call_id, "patient_satisfaction", result)
                previous[call_id] = {
                    "score": result["score"],
                    "state": result["state"],
                    "trend": result["trend"],
                }
                sample_indexes[call_id] = sample_index
                last_turn_count[call_id] = turn_count
                last_sample_at[call_id] = sample_at
        except Exception:  # noqa: BLE001 - evaluation must never affect a call
            logger.exception(
                "patient satisfaction evaluation failed | call_id={}", call_id
            )
        finally:
            tasks.pop(call_id, None)

    def request_sample(call_id: str, final: bool, client: httpx.AsyncClient) -> None:
        requested[call_id] = requested.get(call_id, False) or final
        if call_id not in tasks:
            tasks[call_id] = asyncio.create_task(sample(call_id, client))

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            while True:
                item = await queue.get()
                if isinstance(item, CallUpdate):
                    call_fields.setdefault(item.call_id, {}).update(item.fields)
                    if item.fields.get("ended_at") is not None:
                        request_sample(item.call_id, True, client)
                    continue

                if not isinstance(item, Event):
                    continue
                turn = _spoken_turn(item)
                if turn is None:
                    continue
                conversations.setdefault(item.call_id, []).append(turn)
                request_sample(item.call_id, False, client)
        finally:
            for task in tasks.values():
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)
