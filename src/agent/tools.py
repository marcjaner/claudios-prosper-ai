"""Tool definitions exposed to the booking agent."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_args, get_origin, get_type_hints

from .call_context import ActionProposal, CallContext
from .clinic_api import ClinicApi, ProsperApiError
from .clinic_models import (
    BookRequest,
    CancelRequest,
    OutcomeReason,
    OutcomeRequest,
    RegisterPatientRequest,
    RescheduleRequest,
)
from .models import Tool


@dataclass
class SubmissionRecord:
    action: str
    payload: dict[str, Any]
    response_status: int | None = None
    response: dict[str, Any] | None = None


class ClinicTools:
    def __init__(self, api: ClinicApi, context: CallContext) -> None:
        self._api = api
        self._context = context
        self._submission: SubmissionRecord | None = None

    def functions(self) -> list[Callable[..., Any]]:
        return [
            getattr(self, name)
            for name in (
                "get_clinic_catalogue",
                "search_patients",
                "get_patient_appointments",
                "search_availability",
                "register_patient",
                "prepare_booking",
                "prepare_reschedule",
                "prepare_cancellation",
                "confirm_action",
                "get_call_state",
                "revise_request",
                "submit_no_action",
                "escalate_to_human",
            )
        ]

    def take_submission(self) -> SubmissionRecord | None:
        submission, self._submission = self._submission, None
        return submission

    def search_patients(
        self,
        name: str | None = None,
        national_id: str | None = None,
        phone: str | None = None,
        date_of_birth: str | None = None,
    ) -> dict[str, Any]:
        """Find a patient; exactly one match verifies its patient_id for this call."""
        response = self._api.search_patients(
            name=name,
            national_id=national_id,
            phone=phone,
            date_of_birth=date_of_birth,
        )
        self._context.record_patient_lookup(response)
        return response

    def get_clinic_catalogue(self) -> dict[str, Any]:
        """Return current clinic specialties, providers, locations, types, and plans."""
        return self._api.get_clinic()

    def get_patient_appointments(
        self,
        patient_id: str,
        when: str = "upcoming",
        request_id: str | None = None,
        new_request: bool = False,
    ) -> dict[str, Any]:
        """Retrieve appointments into one request; use appointment_ref for changes."""
        request = self._context.select_request(
            patient_id, request_id=request_id, new_request=new_request
        )
        revision = request.revision
        response = self._api.get_patient_appointments(patient_id, when=when)
        appointments = response.get("appointments", [])
        if not isinstance(appointments, list):
            raise TypeError("invalid_appointments_response")
        result = dict(response)
        if when == "upcoming":
            result["appointments"] = self._context.install_appointments(
                request, appointments, expected_revision=revision
            )
        else:
            result["appointments"] = [
                {
                    key: value
                    for key, value in appointment.items()
                    if key != "appointment_id"
                }
                for appointment in appointments
                if isinstance(appointment, dict)
            ]
        result["request_id"] = request.request_id
        return result

    def search_availability(
        self,
        date_from: str,
        date_to: str,
        patient_id: str,
        provider_id: str | None = None,
        specialty_id: str | None = None,
        location_id: str | None = None,
        insurers: list[str] | None = None,
        request_id: str | None = None,
        new_request: bool = False,
    ) -> dict[str, Any]:
        """Find real slots for one request; use returned slot_id to prepare an action."""
        request = self._context.select_request(
            patient_id, request_id=request_id, new_request=new_request
        )
        revision = request.revision
        criteria = {
            "date_from": date_from,
            "date_to": date_to,
            "provider_id": provider_id,
            "specialty_id": specialty_id,
            "location_id": location_id,
            "insurers": insurers,
        }
        response = self._api.search_availability(
            date_from=date_from,
            date_to=date_to,
            provider_id=provider_id,
            specialty_id=specialty_id,
            location_id=location_id,
            patient_id=patient_id,
            insurers=insurers,
        )
        slots = response.get("slots", [])
        if not isinstance(slots, list):
            raise TypeError("invalid_availability_response")
        result = dict(response)
        result["slots"] = self._context.install_slots(
            request, criteria, slots, expected_revision=revision
        )
        result["request_id"] = request.request_id
        return result

    def register_patient(
        self,
        given_name: str,
        first_surname: str,
        second_surname: str,
        national_id: str,
        date_of_birth: str,
        phone: str,
        email: str,
        insurer: str,
    ) -> dict[str, Any]:
        """Register a caller missing from the directory. Do not book them too."""
        request = RegisterPatientRequest(
            call_id=self._context.call_id,
            given_name=given_name,
            first_surname=first_surname,
            second_surname=second_surname,
            national_id=national_id,
            date_of_birth=date_of_birth,
            phone=phone,
            email=email,
            insurer=insurer,
        )
        return self._submit_direct("REGISTER", request, self._api.register_patient)

    def prepare_booking(self, slot_id: str, policy_id: str) -> dict[str, Any]:
        """Freeze a booking from stored slot evidence without submitting it."""
        return self._proposal_result(self._context.prepare_booking(slot_id, policy_id))

    def prepare_reschedule(
        self, appointment_ref: str, slot_id: str, policy_id: str
    ) -> dict[str, Any]:
        """Freeze a move from same-request appointment and slot evidence."""
        proposal = self._context.prepare_reschedule(appointment_ref, slot_id, policy_id)
        return self._proposal_result(proposal)

    def prepare_cancellation(self, appointment_ref: str) -> dict[str, Any]:
        """Freeze a cancellation from a retrieved upcoming appointment."""
        return self._proposal_result(
            self._context.prepare_cancellation(appointment_ref)
        )

    def confirm_action(
        self, proposal_id: str, confirmed: bool = True
    ) -> dict[str, Any]:
        """Submit a prepared action only after confirmation in a later caller turn."""
        proposal = self._context.begin_submission(proposal_id, confirmed)
        self._submission = SubmissionRecord(proposal.action, dict(proposal.payload))
        try:
            response = self._post_proposal(proposal)
        except ProsperApiError as exc:
            result = {"detail": exc.detail}
            self._submission.response_status = exc.status_code
            self._submission.response = result
            self._context.reject_submission(proposal_id, result)
            raise
        except Exception:
            self._context.mark_submission_uncertain(proposal_id)
            raise
        self._submission.response_status = 200
        self._submission.response = response
        self._context.accept_submission(proposal_id, response)
        return {
            "proposal_id": proposal_id,
            "request_id": proposal.request_id,
            "action": proposal.action,
            "submitted": True,
            "status": self._context.proposals[proposal_id].status,
            "result": response,
        }

    def get_call_state(self) -> dict[str, Any]:
        """Return request IDs, evidence references, and proposal statuses for this call."""
        return self._context.projection()

    def revise_request(self, request_id: str) -> dict[str, Any]:
        """Discard this request's slots and unsubmitted proposals before a correction."""
        request = self._context.revise_request(request_id)
        return {
            "request_id": request.request_id,
            "revision": request.revision,
            "status": request.status,
            "instruction": "Search again with this request_id and corrected criteria.",
        }

    def submit_no_action(self, reason: OutcomeReason) -> dict[str, Any]:
        """Record why the clinic correctly cannot complete this request."""
        request = OutcomeRequest(call_id=self._context.call_id, reason=reason)
        return self._submit_direct("NO_ACTION", request, self._api.no_action)

    def escalate_to_human(self, reason: OutcomeReason) -> dict[str, Any]:
        """Escalate the call, especially a medical emergency, without booking."""
        request = OutcomeRequest(call_id=self._context.call_id, reason=reason)
        return self._submit_direct("ESCALATE", request, self._api.escalate)

    def _post_proposal(self, proposal: ActionProposal) -> dict[str, Any]:
        if proposal.action == "BOOK":
            return self._api.book(BookRequest(**proposal.payload))
        if proposal.action == "RESCHEDULE":
            return self._api.reschedule(RescheduleRequest(**proposal.payload))
        return self._api.cancel(CancelRequest(**proposal.payload))

    def _submit_direct(
        self, action: str, request: Any, submit: Callable
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        self._submission = SubmissionRecord(action, payload)
        try:
            response = submit(request)
        except ProsperApiError as exc:
            self._submission.response_status = exc.status_code
            self._submission.response = {"detail": exc.detail}
            raise
        self._submission.response_status = 200
        self._submission.response = response
        return response

    @staticmethod
    def _proposal_result(proposal: ActionProposal) -> dict[str, Any]:
        details = {
            key: value for key, value in proposal.payload.items() if key != "call_id"
        }
        return {
            "proposal_id": proposal.proposal_id,
            "request_id": proposal.request_id,
            "action": proposal.action,
            "details": details,
            "readback": ", ".join(f"{key}: {value}" for key, value in details.items()),
            "submitted": False,
            "instruction": (
                "Read back these exact details and wait for a new caller turn "
                "before confirm_action."
            ),
        }


def create_clinic_tools(
    api: ClinicApi, call_id: str, context: CallContext | None = None
) -> list[Callable[..., Any]]:
    return ClinicTools(api, context or CallContext(call_id)).functions()


def load_tools(functions: list[Callable[..., Any]]) -> dict[str, Tool]:
    loaded: dict[str, Tool] = {}
    for function in functions:
        function_name = getattr(function, "__name__", type(function).__name__)
        hints = get_type_hints(function)
        properties = {
            name: _json_schema(hints.get(name, str))
            for name in inspect.signature(function).parameters
        }
        required = [
            name
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.default is inspect.Parameter.empty
        ]
        loaded[function_name] = Tool(
            name=function_name,
            description=inspect.getdoc(function) or "",
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            execute=function,
        )
    return loaded


def _json_schema(annotation: Any) -> dict[str, Any]:
    if annotation is str or annotation is OutcomeReason:
        schema = {"type": "string"}
        if annotation is OutcomeReason:
            schema["enum"] = [reason.value for reason in OutcomeReason]
        return schema
    if annotation in (int, float):
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if get_origin(annotation) is list:
        return {"type": "array", "items": _json_schema(get_args(annotation)[0])}
    if get_origin(annotation) is not None and type(None) in get_args(annotation):
        return _json_schema(
            next(item for item in get_args(annotation) if item is not type(None))
        )
    return {"type": "object"}
