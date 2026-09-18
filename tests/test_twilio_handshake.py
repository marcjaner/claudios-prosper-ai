import pytest

from twilio.handshake import parse_start


def start_message(**start_overrides) -> dict:
    start = {
        "streamSid": "MZ123",
        "callSid": "CA456",
        "customParameters": {"call_id": "CA456", "from_number": "+34612345678"},
    }
    start.update(start_overrides)
    return {"event": "start", "streamSid": "MZ123", "start": start}


def test_reads_call_identity():
    meta = parse_start(start_message())
    assert meta.call_id == "CA456"
    assert meta.stream_sid == "MZ123"
    assert meta.from_number == "+34612345678"


def test_withheld_caller_id_is_none_not_an_error():
    meta = parse_start(start_message(customParameters={"call_id": "CA456"}))
    assert meta.from_number is None


def test_call_sid_wins_over_the_custom_parameter():
    meta = parse_start(start_message(customParameters={"call_id": "CA-OTHER"}))
    assert meta.call_id == "CA456"


def test_missing_identity_raises():
    with pytest.raises(ValueError):
        parse_start(start_message(callSid="", customParameters={}))
