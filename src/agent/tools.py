"""Tool definitions exposed to the booking agent."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, get_args, get_origin, get_type_hints

from .clinic_api import ClinicApi
from .clinic_models import (
    BookRequest,
    CancelRequest,
    OutcomeReason,
    OutcomeRequest,
    RegisterPatientRequest,
    RescheduleRequest,
)

_logger = logging.getLogger(__name__)


class ClinicTools:
    """Per-call tools. The model never receives control of ``call_id``."""

    def __init__(self, api: ClinicApi, call_id: str) -> None:
        self._api = api
        self._call_id = call_id

    def search_patients(
        self,
        name: str | None = None,
        national_id: str | None = None,
        phone: str | None = None,
        date_of_birth: str | None = None,
    ) -> dict[str, Any]:
        """Find patients by the exact identifiers collected from the caller."""
        return self._api.search_patients(
            name=name,
            national_id=national_id,
            phone=phone,
            date_of_birth=date_of_birth,
        )

    def get_patient_appointments(
        self, patient_id: str, when: str = "upcoming"
    ) -> dict[str, Any]:
        """Return this patient's appointments; only upcoming ones can change."""
        return self._api.get_patient_appointments(patient_id, when=when)

    def search_availability(
        self,
        date_from: str,
        date_to: str,
        provider_id: str | None = None,
        specialty_id: str | None = None,
        location_id: str | None = None,
        patient_id: str | None = None,
        insurers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return real bookable slots, the matching type, and any blocked reasons."""
        return self._api.search_availability(
            date_from=date_from,
            date_to=date_to,
            provider_id=provider_id,
            specialty_id=specialty_id,
            location_id=location_id,
            patient_id=patient_id,
            insurers=insurers,
        )

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
        return self._api.register_patient(
            RegisterPatientRequest(
                call_id=self._call_id,
                given_name=given_name,
                first_surname=first_surname,
                second_surname=second_surname,
                national_id=national_id,
                date_of_birth=date_of_birth,
                phone=phone,
                email=email,
                insurer=insurer,
            )
        )

    def book_appointment(
        self,
        patient_id: str,
        provider_id: str,
        location_id: str,
        appointment_type_id: str,
        slot: str,
        policy_id: str,
    ) -> dict[str, Any]:
        """Submit a booking using IDs and slot returned by Prosper."""
        return self._api.book(
            BookRequest(
                call_id=self._call_id,
                patient_id=patient_id,
                provider_id=provider_id,
                location_id=location_id,
                appointment_type_id=appointment_type_id,
                slot=slot,
                policy_id=policy_id,
            )
        )

    def reschedule_appointment(
        self,
        appointment_id: str,
        provider_id: str,
        location_id: str,
        slot: str,
        policy_id: str,
    ) -> dict[str, Any]:
        """Move an existing upcoming appointment to a returned slot."""
        return self._api.reschedule(
            RescheduleRequest(
                call_id=self._call_id,
                appointment_id=appointment_id,
                provider_id=provider_id,
                location_id=location_id,
                slot=slot,
                policy_id=policy_id,
            )
        )

    def cancel_appointment(self, appointment_id: str) -> dict[str, Any]:
        """Cancel one existing upcoming appointment."""
        return self._api.cancel(CancelRequest(call_id=self._call_id, appointment_id=appointment_id))

    def submit_no_action(self, reason: OutcomeReason) -> dict[str, Any]:
        """Record why the clinic correctly cannot complete this request."""
        return self._api.no_action(OutcomeRequest(call_id=self._call_id, reason=reason))

    def escalate_to_human(self, reason: OutcomeReason) -> dict[str, Any]:
        """Escalate the call, especially a medical emergency, without booking."""
        return self._api.escalate(OutcomeRequest(call_id=self._call_id, reason=reason))


def create_clinic_tools(api: ClinicApi, call_id: str) -> list[Callable[..., Any]]:
    """Create isolated tools for one socket/call."""
    tools = ClinicTools(api, call_id)
    return [
        tools.search_patients,
        tools.get_patient_appointments,
        tools.search_availability,
        tools.register_patient,
        tools.book_appointment,
        tools.reschedule_appointment,
        tools.cancel_appointment,
        tools.submit_no_action,
        tools.escalate_to_human,
    ]


def load_tools(functions: list[Callable[..., Any]]) -> dict[str, dict[str, Any]]:
    """Build model definitions and executable functions from tool callables."""
    _logger.debug("Loading %d tools: %s", len(functions), [f.__name__ for f in functions])
    loaded: dict[str, dict[str, Any]] = {}
    for function in functions:
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, parameter in signature.parameters.items():
            annotation = hints.get(name, str)
            properties[name] = _json_schema(annotation)
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
        loaded[function.__name__] = {
            "definition": {
                "type": "function",
                "function": {
                    "name": function.__name__,
                    "description": inspect.getdoc(function) or "",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            },
            "execute": function,
        }
        _logger.debug("Loaded tool %s with schema %s", function.__name__, properties)
    _logger.info("Loaded tools: %s", list(loaded))
    return loaded


def _json_schema(annotation: Any) -> dict[str, Any]:
    """Translate the small set of tool argument types used by this project."""
    if annotation is str or annotation is OutcomeReason:
        schema: dict[str, Any] = {"type": "string"}
        if annotation is OutcomeReason:
            schema["enum"] = [reason.value for reason in OutcomeReason]
        return schema
    if annotation in (int, float):
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    origin = get_origin(annotation)
    if origin is list:
        return {"type": "array", "items": _json_schema(get_args(annotation)[0])}
    if origin is not None and type(None) in get_args(annotation):
        non_none = next(item for item in get_args(annotation) if item is not type(None))
        return _json_schema(non_none)
    return {"type": "object"}
