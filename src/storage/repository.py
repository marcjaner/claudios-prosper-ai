from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from .database import Database
from .models import ApiObservation, Call, CallEvent, ClinicCatalogue, Guardrail, Submission


def now() -> datetime:
    return datetime.now(UTC)


DEFAULT_GUARDRAILS = [
    ("Stay on scheduling", "Stay focused on appointment booking, rescheduling, cancellation, and clinic scheduling questions."),
    ("No medical advice", "Do not provide medical advice or diagnose symptoms; recommend contacting a healthcare professional when needed."),
    ("Protect privacy", "Protect patient privacy and verify identity before sharing appointment or patient information."),
    ("Use real availability", "Only offer appointment times returned by the clinic system. Never invent availability, providers, or policies."),
    ("Escalate when needed", "Escalate urgent, unsafe, or out-of-scope requests to a human instead of guessing."),
]


class CallRepository:
    def __init__(self, database: Database):
        self.database = database

    async def list_guardrails(self) -> list[Guardrail]:
        async with self.database.session() as session:
            result = await session.scalars(
                select(Guardrail)
                .where(Guardrail.is_active.is_(True))
                .order_by(Guardrail.position, Guardrail.id)
            )
            return list(result)

    async def seed_default_guardrails(self) -> None:
        async with self.database.session() as session, session.begin():
            active = list(await session.scalars(select(Guardrail).where(Guardrail.is_active.is_(True)).order_by(Guardrail.position, Guardrail.id)))
            if active:
                for index, guardrail in enumerate(active[: len(DEFAULT_GUARDRAILS)]):
                    if guardrail.title == "Safety rule":
                        guardrail.title = DEFAULT_GUARDRAILS[index][0]
                        guardrail.description = guardrail.description or guardrail.text
                return
            timestamp = now()
            session.add_all(
                Guardrail(title=title, text=description, description=description, position=index, is_active=True, created_at=timestamp, updated_at=timestamp)
                for index, (title, description) in enumerate(DEFAULT_GUARDRAILS)
            )

    async def replace_guardrails(self, rules: list[dict[str, str]]) -> list[Guardrail]:
        cleaned = []
        for rule in rules:
            title = rule.get("title", "").strip()
            description = rule.get("description", "").strip()
            if title or description:
                if not title or len(title) > 80:
                    raise ValueError("Safety rule titles must be between 1 and 80 characters")
                cleaned.append((title, description))
        async with self.database.session() as session, session.begin():
            current = await session.scalars(select(Guardrail))
            for guardrail in current:
                guardrail.is_active = False
                guardrail.updated_at = now()
            created = [
                Guardrail(title=title, text=description, description=description, position=index, created_at=now(), updated_at=now())
                for index, (title, description) in enumerate(cleaned)
            ]
            session.add_all(created)
        return created

    async def save_catalogue(self, payload: dict[str, Any]) -> ClinicCatalogue:
        async with self.database.session() as session, session.begin():
            await session.execute(
                update(ClinicCatalogue)
                .where(ClinicCatalogue.is_current.is_(True))
                .values(is_current=False)
            )
            catalogue = ClinicCatalogue(fetched_at=now(), payload=payload)
            session.add(catalogue)
        return catalogue

    async def create_call(
        self, call_id: str, from_number_hint: str | None, started_at: datetime
    ) -> Call:
        async with self.database.session() as session, session.begin():
            call = Call(
                call_id=call_id,
                from_number_hint=from_number_hint,
                started_at=started_at,
                workflow_stage="identify",
                workflow_state={},
            )
            session.add(call)
        return call

    async def workflow_for_call(self, call_id: str) -> dict[str, Any]:
        async with self.database.session() as session:
            call = await session.get(Call, call_id)
            if call is None:
                raise ValueError(f"unknown call_id: {call_id}")
            return {"stage": call.workflow_stage, **(call.workflow_state or {})}

    async def save_workflow(self, call_id: str, state: dict[str, Any]) -> None:
        async with self.database.session() as session, session.begin():
            call = await session.get(Call, call_id)
            if call is None:
                raise ValueError(f"unknown call_id: {call_id}")
            call.workflow_stage = state.get("stage", "identify")
            call.workflow_state = {key: value for key, value in state.items() if key != "stage"}

    async def append_event(
        self, call_id: str, event_type: str, payload: dict[str, Any]
    ) -> CallEvent:
        async with self.database.session() as session, session.begin():
            event = CallEvent(
                call_id=call_id,
                occurred_at=now(),
                event_type=event_type,
                payload=payload,
            )
            session.add(event)
        return event

    async def record_api_observation(
        self,
        call_id: str | None,
        endpoint: str,
        request: dict[str, Any],
        response_status: int | None,
        response: dict[str, Any] | None,
        duration_ms: int | None,
    ) -> ApiObservation:
        async with self.database.session() as session, session.begin():
            observation = ApiObservation(
                call_id=call_id,
                occurred_at=now(),
                endpoint=endpoint,
                request=request,
                response_status=response_status,
                response=response,
                duration_ms=duration_ms,
            )
            session.add(observation)
        return observation

    async def record_submission(
        self,
        call_id: str,
        action: str,
        request: dict[str, Any],
        response_status: int | None,
        response: dict[str, Any] | None,
    ) -> Submission:
        async with self.database.session() as session, session.begin():
            submission = Submission(
                call_id=call_id,
                submitted_at=now(),
                action=action,
                request=request,
                response_status=response_status,
                response=response,
            )
            session.add(submission)
        return submission

    async def finish_call(self, call_id: str, outcome: str) -> None:
        async with self.database.session() as session, session.begin():
            call = await session.get(Call, call_id)
            if call is None:
                raise ValueError(f"unknown call_id: {call_id}")
            call.ended_at = now()
            call.status = "finished"
            call.final_outcome = outcome

    async def events_for_call(self, call_id: str) -> list[CallEvent]:
        async with self.database.session() as session:
            result = await session.scalars(
                select(CallEvent)
                .where(CallEvent.call_id == call_id)
                .order_by(CallEvent.id)
            )
            return list(result)

    async def memory_for_call(self, call_id: str) -> str:
        events = await self.events_for_call(call_id)
        return "\n".join(
            f"{event.event_type}: {event.payload}" for event in events
        )
