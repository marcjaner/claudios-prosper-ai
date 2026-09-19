import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

CLINIC_TIMEZONE = ZoneInfo("Europe/Madrid")
HANDSHAKE_TIMEOUT_SECONDS = 10.0
MAX_HANDSHAKE_MESSAGES = 20


@dataclass(frozen=True)
class CallMeta:
    call_id: str
    stream_sid: str
    from_number: str | None
    connected_at: datetime


def parse_start(message: dict) -> CallMeta:
    start = message.get("start") or {}
    parameters = {
        str(key): str(value)
        for key, value in (start.get("customParameters") or {}).items()
        if value is not None
    }

    # Submissions are attributed by callSid; customParameters repeats it only
    # for convenience, so it is the fallback rather than the source.
    call_id = start.get("callSid") or parameters.get("call_id")
    if not call_id:
        raise ValueError("start message carries no callSid")

    return CallMeta(
        call_id=call_id,
        stream_sid=start.get("streamSid") or message.get("streamSid", ""),
        # Absent means the caller id was withheld, which is a real call the
        # agent has to handle, not a malformed handshake.
        from_number=parameters.get("from_number"),
        # Every relative date the caller says resolves against this instant.
        connected_at=datetime.now(CLINIC_TIMEZONE),
    )


async def read_handshake(websocket) -> CallMeta:
    for _ in range(MAX_HANDSHAKE_MESSAGES):
        raw = await asyncio.wait_for(
            websocket.receive_text(), HANDSHAKE_TIMEOUT_SECONDS
        )
        message = json.loads(raw)
        if message.get("event") == "start":
            return parse_start(message)

    raise ValueError("no start event in the handshake")
