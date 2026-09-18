from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from .database import Database
from .models import ApiObservation, Call, CallEvent, ClinicCatalogue, Submission


def now() -> datetime:
    return datetime.now(UTC)


class CallRepository:
    def __init__(self, database: Database):
        self.database = database

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
            )
            session.add(call)
        return call

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
