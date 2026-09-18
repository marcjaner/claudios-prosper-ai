import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from storage import CallRepository, Database
from storage.models import Call, ClinicCatalogue, Submission


def test_records_a_complete_call_without_mixing_events(tmp_path):
    async def scenario():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}")
        await database.init()
        repository = CallRepository(database)

        await repository.save_catalogue({"clinic_name": "Clínica Arenal"})
        await repository.create_call("CA-1", "+34612345678", datetime.now(UTC))
        await repository.create_call("CA-2", None, datetime.now(UTC))
        await repository.append_event("CA-1", "caller_text_received", {"text": "Hola"})
        await repository.append_event("CA-2", "caller_text_received", {"text": "Hello"})
        await repository.record_api_observation(
            "CA-1",
            "/api/v1/directory",
            {"phone": "+34612345678"},
            200,
            {"matches": [{"id": "P00042"}]},
            12,
        )
        await repository.record_submission(
            "CA-1", "BOOK", {"patient_id": "P00042"}, 200, {"record": {}}
        )
        await repository.finish_call("CA-1", "BOOK")

        first_call_events = await repository.events_for_call("CA-1")
        second_call_events = await repository.events_for_call("CA-2")
        assert [event.payload["text"] for event in first_call_events] == ["Hola"]
        assert [event.payload["text"] for event in second_call_events] == ["Hello"]

        async with database.session() as session:
            call = await session.get(Call, "CA-1")
            catalogue = await session.scalar(
                select(ClinicCatalogue).where(ClinicCatalogue.is_current.is_(True))
            )
            submission = await session.scalar(
                select(Submission).where(Submission.call_id == "CA-1")
            )

        assert call.status == "finished"
        assert call.final_outcome == "BOOK"
        assert catalogue.payload["clinic_name"] == "Clínica Arenal"
        assert submission.action == "BOOK"
        await database.close()

    asyncio.run(scenario())
