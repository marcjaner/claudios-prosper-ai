from typing import Any, cast

import pytest

from agent.call_context import CallContext
from agent.clinic_api import ClinicApi
from agent.tools import ClinicTools

PATIENT_ID = "P00042"
SLOT = {
    "provider_id": "PR05",
    "provider_name": "Dra. Vega",
    "specialty_id": "dermatology",
    "location_id": "sur",
    "appointment_type_id": "review",
    "start_time": "2026-09-24T16:30:00+02:00",
    "duration_minutes": 30,
    "payable_with": ["sanitas"],
}
APPOINTMENT = {
    "appointment_id": "A000123",
    "patient_id": PATIENT_ID,
    "provider_id": "PR01",
    "location_id": "norte",
    "appointment_type_id": "initial",
    "start_time": "2026-09-22T10:00:00+02:00",
    "duration_minutes": 30,
}


class FakeClinicApi:
    def __init__(self) -> None:
        self.slots = [dict(SLOT)]
        self.appointments = [dict(APPOINTMENT)]
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.timeout = False

    def search_patients(self, **_kwargs):
        return {"matches": [{"patient_id": PATIENT_ID}]}

    def search_availability(self, **_kwargs):
        return {"slots": [dict(slot) for slot in self.slots]}

    def get_patient_appointments(self, _patient_id, *, when="upcoming"):
        assert when == "upcoming"
        return {
            "appointments": [dict(appointment) for appointment in self.appointments]
        }

    def book(self, request):
        return self._post("BOOK", request)

    def reschedule(self, request):
        return self._post("RESCHEDULE", request)

    def cancel(self, request):
        return self._post("CANCEL", request)

    def _post(self, action, request):
        self.posts.append((action, request.model_dump(mode="json")))
        if self.timeout:
            raise TimeoutError("submission outcome unknown")
        return {"record": {"actions": [{"action": action}]}}


def verified_tools(
    call_id: str = "CA-1",
) -> tuple[FakeClinicApi, CallContext, ClinicTools]:
    api = FakeClinicApi()
    context = CallContext(call_id)
    tools = ClinicTools(cast(ClinicApi, api), context)
    tools.search_patients(national_id="12345678Z")
    return api, context, tools


def search_slot(tools: ClinicTools, **kwargs) -> dict[str, Any]:
    return tools.search_availability(
        "2026-09-24",
        "2026-09-24",
        PATIENT_ID,
        specialty_id="dermatology",
        **kwargs,
    )


def test_context_survives_tool_instances_and_is_isolated_per_call():
    first_api, first_context, first_tools = verified_tools("CA-1")
    _, second_context, _ = verified_tools("CA-2")
    first_context.begin_turn()
    slot_id = search_slot(first_tools)["slots"][0]["slot_id"]

    next_turn_tools = ClinicTools(cast(ClinicApi, first_api), first_context)
    proposal = next_turn_tools.prepare_booking(slot_id, "sanitas")

    assert proposal["request_id"] == "req_1"
    assert proposal["proposal_id"] == "prop_1"
    assert second_context.requests == {}
    assert second_context.proposals == {}


def test_distinct_request_and_refinement_only_invalidate_the_target_request():
    _, context, tools = verified_tools()
    context.begin_turn()
    first = search_slot(tools)
    first_slot_id = first["slots"][0]["slot_id"]
    first_proposal = tools.prepare_booking(first_slot_id, "sanitas")
    second = search_slot(tools, new_request=True)

    replacement = search_slot(tools, request_id="req_1")

    assert first["request_id"] == "req_1"
    assert second["request_id"] == "req_2"
    assert replacement["slots"][0]["slot_id"] != first_slot_id
    assert context.proposals[first_proposal["proposal_id"]].status == "rejected"
    assert context.requests["req_2"].slots
    with pytest.raises(ValueError, match="slot_not_found_or_stale"):
        tools.prepare_booking(first_slot_id, "sanitas")


def test_booking_uses_evidence_and_requires_a_later_turn_confirmation():
    api, context, tools = verified_tools()
    context.begin_turn()
    slot_id = search_slot(tools)["slots"][0]["slot_id"]
    proposal_id = tools.prepare_booking(slot_id, "sanitas")["proposal_id"]

    with pytest.raises(ValueError, match="confirmation_requires_new_caller_turn"):
        tools.confirm_action(proposal_id)
    assert api.posts == []

    context.begin_turn()
    result = tools.confirm_action(proposal_id)

    assert result["status"] == "accepted"
    assert api.posts == [
        (
            "BOOK",
            {
                "call_id": "CA-1",
                "patient_id": PATIENT_ID,
                "provider_id": SLOT["provider_id"],
                "location_id": SLOT["location_id"],
                "appointment_type_id": SLOT["appointment_type_id"],
                "slot": SLOT["start_time"],
                "policy_id": "sanitas",
            },
        )
    ]
    with pytest.raises(ValueError, match="proposal_not_confirmable"):
        tools.confirm_action(proposal_id)
    assert len(api.posts) == 1


def test_reschedule_rejects_cross_request_evidence_and_cancel_requires_lookup():
    _, context, tools = verified_tools()
    context.begin_turn()
    appointments = tools.get_patient_appointments(PATIENT_ID)
    appointment_ref = appointments["appointments"][0]["appointment_ref"]
    separate = search_slot(tools, new_request=True)

    with pytest.raises(ValueError, match="cross_request_evidence"):
        tools.prepare_reschedule(
            appointment_ref, separate["slots"][0]["slot_id"], "sanitas"
        )
    with pytest.raises(ValueError, match="appointment_not_found_or_stale"):
        tools.prepare_cancellation("appt_invented")


def test_two_cancellations_can_complete_as_separate_requests():
    api, context, tools = verified_tools()
    context.begin_turn()
    first_ref = tools.get_patient_appointments(PATIENT_ID)["appointments"][0][
        "appointment_ref"
    ]
    first_proposal = tools.prepare_cancellation(first_ref)["proposal_id"]
    context.begin_turn()
    tools.confirm_action(first_proposal)

    api.appointments[0]["appointment_id"] = "A000124"
    second_ref = tools.get_patient_appointments(PATIENT_ID, new_request=True)[
        "appointments"
    ][0]["appointment_ref"]
    second_proposal = tools.prepare_cancellation(second_ref)["proposal_id"]
    context.begin_turn()
    tools.confirm_action(second_proposal)

    assert [payload["appointment_id"] for _, payload in api.posts] == [
        "A000123",
        "A000124",
    ]


def test_timeout_marks_proposal_uncertain_and_blocks_replacement():
    api, context, tools = verified_tools()
    context.begin_turn()
    slot_id = search_slot(tools)["slots"][0]["slot_id"]
    proposal_id = tools.prepare_booking(slot_id, "sanitas")["proposal_id"]
    context.begin_turn()
    api.timeout = True

    with pytest.raises(TimeoutError):
        tools.confirm_action(proposal_id)

    assert context.proposals[proposal_id].status == "uncertain"
    with pytest.raises(ValueError, match="request_has_uncertain_submission"):
        tools.revise_request("req_1")
