import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from storage import CallRepository, Database

DATABASE_URL = "sqlite+aiosqlite:///data/storage-smoke.db"


async def main() -> None:
    database = Database(DATABASE_URL)
    await database.init()
    repository = CallRepository(database)
    call_id = f"smoke-{uuid4()}"

    await repository.save_catalogue({"clinic_name": "Clínica Arenal"})
    await repository.create_call(call_id, "+34612345678", datetime.now(UTC))
    await repository.append_event(
        call_id, "caller_text_received", {"text": "I need an appointment."}
    )
    await repository.record_submission(
        call_id,
        "NO_ACTION",
        {"reason": "no_availability"},
        200,
        {"record": {"actions": []}},
    )
    await repository.finish_call(call_id, "NO_ACTION")

    events = await repository.events_for_call(call_id)
    print(f"Saved call: {call_id}")
    print(f"Stored events: {len(events)}")
    print("Database: data/storage-smoke.db")
    await database.close()


if __name__ == "__main__":
    asyncio.run(main())
