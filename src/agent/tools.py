"""Tool definitions exposed to the booking agent."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_args, get_origin, get_type_hints

from .clinic_api import ClinicApi
from .clinic_models import (
    BookRequest,
    CancelRequest,
    OutcomeReason,
    OutcomeRequest,
    RegisterPatientRequest,
    RescheduleRequest,
)
from .models import Tool


class ClinicTools:
    def __init__(self, api: ClinicApi, call_id: str) -> None:
        self._api, self._call_id = api, call_id

    def search_patients(self, name: str | None = None, national_id: str | None = None, phone: str | None = None, date_of_birth: str | None = None) -> dict[str, Any]:
        """Find patients by the exact identifiers collected from the caller."""
        return self._api.search_patients(name=name, national_id=national_id, phone=phone, date_of_birth=date_of_birth)

    def get_clinic_catalogue(self) -> dict[str, Any]:
        """Return specialties, providers, locations, appointment types, and policies with their IDs."""
        return self._api.get_clinic()

    def get_patient_appointments(self, patient_id: str, when: str = "upcoming") -> dict[str, Any]:
        """Return this patient's appointments; only upcoming ones can change."""
        return self._api.get_patient_appointments(patient_id, when=when)

    def search_availability(self, date_from: str, date_to: str, provider_id: str | None = None, specialty_id: str | None = None, location_id: str | None = None, patient_id: str | None = None, insurers: list[str] | None = None) -> dict[str, Any]:
        """Return real bookable slots, the matching type, and blocked reasons."""
        return self._api.search_availability(date_from=date_from, date_to=date_to, provider_id=provider_id, specialty_id=specialty_id, location_id=location_id, patient_id=patient_id, insurers=insurers)

    def register_patient(self, given_name: str, first_surname: str, second_surname: str, national_id: str, date_of_birth: str, phone: str, email: str, insurer: str) -> dict[str, Any]:
        """Register a caller missing from the directory. Do not book them too."""
        return self._api.register_patient(RegisterPatientRequest(call_id=self._call_id, given_name=given_name, first_surname=first_surname, second_surname=second_surname, national_id=national_id, date_of_birth=date_of_birth, phone=phone, email=email, insurer=insurer))

    def book_appointment(self, patient_id: str, provider_id: str, location_id: str, appointment_type_id: str, slot: str, policy_id: str) -> dict[str, Any]:
        """Submit a booking using IDs and a slot returned by Prosper."""
        return self._api.book(BookRequest(call_id=self._call_id, patient_id=patient_id, provider_id=provider_id, location_id=location_id, appointment_type_id=appointment_type_id, slot=slot, policy_id=policy_id))

    def reschedule_appointment(self, appointment_id: str, provider_id: str, location_id: str, slot: str, policy_id: str) -> dict[str, Any]:
        """Move an existing upcoming appointment to a returned slot."""
        return self._api.reschedule(RescheduleRequest(call_id=self._call_id, appointment_id=appointment_id, provider_id=provider_id, location_id=location_id, slot=slot, policy_id=policy_id))

    def cancel_appointment(self, appointment_id: str) -> dict[str, Any]:
        """Cancel one existing upcoming appointment."""
        return self._api.cancel(CancelRequest(call_id=self._call_id, appointment_id=appointment_id))

    def submit_no_action(self, reason: OutcomeReason) -> dict[str, Any]:
        """Record why the clinic correctly cannot complete this request."""
        return self._api.no_action(OutcomeRequest(call_id=self._call_id, reason=reason))

    def escalate_to_human(self, reason: OutcomeReason) -> dict[str, Any]:
        """Escalate the call, especially a medical emergency, without booking."""
        return self._api.escalate(OutcomeRequest(call_id=self._call_id, reason=reason))


CLINIC_TOOL_NAMES = (
    "get_clinic_catalogue", "search_patients", "get_patient_appointments", "search_availability",
    "register_patient", "book_appointment", "reschedule_appointment",
    "cancel_appointment", "submit_no_action", "escalate_to_human",
)


# Tools that POST a record to Prosper. A failed one may still have been received,
# which is what makes retrying it dangerous; a failed lookup carries no such doubt.
SUBMISSION_TOOL_NAMES = frozenset({
    "register_patient", "book_appointment", "reschedule_appointment",
    "cancel_appointment", "submit_no_action", "escalate_to_human",
})


def create_clinic_tools(api: ClinicApi, call_id: str) -> list[Callable[..., Any]]:
    tools = ClinicTools(api, call_id)
    return [getattr(tools, name) for name in CLINIC_TOOL_NAMES]


def load_tools(functions: list[Callable[..., Any]]) -> dict[str, Tool]:
    loaded: dict[str, Tool] = {}
    for function in functions:
        function_name = getattr(function, "__name__", type(function).__name__)
        hints = get_type_hints(function)
        properties = {name: _json_schema(hints.get(name, str)) for name in inspect.signature(function).parameters}
        required = [name for name, parameter in inspect.signature(function).parameters.items() if parameter.default is inspect.Parameter.empty]
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
    if annotation in (int, float): return {"type": "number"}
    if annotation is bool: return {"type": "boolean"}
    if get_origin(annotation) is list: return {"type": "array", "items": _json_schema(get_args(annotation)[0])}
    if get_origin(annotation) is not None and type(None) in get_args(annotation):
        return _json_schema(next(item for item in get_args(annotation) if item is not type(None)))
    return {"type": "object"}
