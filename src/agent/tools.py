"""Tool definitions exposed to the booking agent."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from enum import Enum
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from .clinic_api import ClinicApi
from .clinic_models import (
    BookRequest,
    CancelRequest,
    OutcomeReason,
    OutcomeRequest,
    RegisterPatientRequest,
    RescheduleRequest,
)

DateValue = Annotated[str, "ISO date in YYYY-MM-DD format."]
AvailabilityStartDate = Annotated[
    str,
    "Inclusive search start in YYYY-MM-DD format. Never use a past or same-day date.",
]
AvailabilityEndDate = Annotated[
    str,
    "Inclusive search end in YYYY-MM-DD format. The range may contain at most 14 days.",
]
DateTimeValue = Annotated[
    str,
    "Exact ISO 8601 date-time returned by availability, including its timezone offset.",
]
PatientId = Annotated[str, "Exact patient_id returned by search_patients."]
AppointmentId = Annotated[
    str, "Exact appointment_id returned by get_patient_appointments."
]
ProviderId = Literal[
    "PR01",
    "PR02",
    "PR03",
    "PR04",
    "PR05",
    "PR06",
    "PR07",
    "PR08",
    "PR09",
    "PR10",
    "PR11",
    "PR12",
]
SpecialtyId = Literal[
    "general_practice",
    "paediatrics",
    "dermatology",
    "orthopaedics",
    "gynaecology",
    "physiotherapy",
]
LocationId = Literal["centro", "norte", "sur"]
AppointmentTypeId = Literal[
    "first_visit",
    "review",
    "paediatric_first_visit",
    "paediatric_review",
    "gynaecology_review",
    "physiotherapy_assessment",
    "physiotherapy_session",
    "dermatology_first_visit",
    "dermatology_review",
    "orthopaedic_first_visit",
    "orthopaedic_review",
]
InsurerId = Literal[
    "sanitas",
    "adeslas",
    "dkv",
    "asisa",
    "mapfre",
    "caser",
    "cigna",
    "axa",
    "nueva_mutua",
    "privado",
]
AppointmentWindow = Literal["upcoming", "past", "all"]


class ClinicTools:
    def __init__(self, api: ClinicApi, call_id: str) -> None:
        self._api, self._call_id = api, call_id

    def search_patients(
        self,
        name: str | None = None,
        national_id: str | None = None,
        phone: str | None = None,
        date_of_birth: DateValue | None = None,
    ) -> dict[str, Any]:
        """Find patients by the exact identifiers collected from the caller."""
        return self._api.search_patients(
            name=name, national_id=national_id, phone=phone, date_of_birth=date_of_birth
        )

    def get_clinic_catalogue(self) -> dict[str, Any]:
        """Return specialties, providers, locations, appointment types, and policies with their IDs."""
        return self._api.get_clinic()

    def get_patient_appointments(
        self, patient_id: PatientId, when: AppointmentWindow = "upcoming"
    ) -> dict[str, Any]:
        """Return a patient's appointments. Only upcoming appointments can be changed."""
        return self._api.get_patient_appointments(patient_id, when=when)

    def search_availability(
        self,
        date_from: AvailabilityStartDate,
        date_to: AvailabilityEndDate,
        provider_id: ProviderId | None = None,
        specialty_id: SpecialtyId | None = None,
        location_id: LocationId | None = None,
        patient_id: PatientId | None = None,
        insurers: list[InsurerId] | None = None,
    ) -> dict[str, Any]:
        """Return bookable slots and blocked reasons. Use either provider_id or specialty_id, exact catalogue IDs, and no more than 14 inclusive days."""
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
        date_of_birth: DateValue,
        phone: str,
        email: str,
        insurer: InsurerId,
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
        patient_id: PatientId,
        provider_id: ProviderId,
        location_id: LocationId,
        appointment_type_id: AppointmentTypeId,
        slot: DateTimeValue,
        policy_id: InsurerId,
    ) -> dict[str, Any]:
        """Submit a booking using IDs and a slot returned by Prosper."""
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
        appointment_id: AppointmentId,
        provider_id: ProviderId,
        location_id: LocationId,
        slot: DateTimeValue,
        policy_id: InsurerId,
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

    def cancel_appointment(self, appointment_id: AppointmentId) -> dict[str, Any]:
        """Cancel one existing upcoming appointment."""
        return self._api.cancel(
            CancelRequest(call_id=self._call_id, appointment_id=appointment_id)
        )

    def submit_no_action(self, reason: OutcomeReason) -> dict[str, Any]:
        """Record why the clinic correctly cannot complete this request."""
        return self._api.no_action(OutcomeRequest(call_id=self._call_id, reason=reason))

    def escalate_to_human(self, reason: OutcomeReason) -> dict[str, Any]:
        """Escalate the call, especially a medical emergency, without booking."""
        return self._api.escalate(OutcomeRequest(call_id=self._call_id, reason=reason))


def create_clinic_tools(api: ClinicApi, call_id: str) -> list[Callable[..., Any]]:
    tools = ClinicTools(api, call_id)
    return [
        getattr(tools, name)
        for name in (
            "get_clinic_catalogue",
            "search_patients",
            "get_patient_appointments",
            "search_availability",
            "register_patient",
            "book_appointment",
            "reschedule_appointment",
            "cancel_appointment",
            "submit_no_action",
            "escalate_to_human",
        )
    ]


def load_tools(functions: list[Callable[..., Any]]) -> dict[str, dict[str, Any]]:
    loaded = {}
    for function in functions:
        function_name = getattr(function, "__name__", type(function).__name__)
        hints = get_type_hints(function, include_extras=True)
        properties = {
            name: _json_schema(hints.get(name, str))
            for name in inspect.signature(function).parameters
        }
        required = [
            name
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.default is inspect.Parameter.empty
        ]
        loaded[function_name] = {
            "definition": {
                "type": "function",
                "function": {
                    "name": function_name,
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
    return loaded


def _json_schema(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    if origin is Annotated:
        value_type, *metadata = get_args(annotation)
        schema = _json_schema(value_type)
        description = next((item for item in metadata if isinstance(item, str)), None)
        if description:
            schema["description"] = description
        return schema
    if origin is Literal:
        values = list(get_args(annotation))
        return {"type": "string", "enum": values}
    if annotation is str:
        return {"type": "string"}
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [item.value for item in annotation]}
    if annotation in (int, float):
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if origin is list:
        return {"type": "array", "items": _json_schema(get_args(annotation)[0])}
    if origin is not None and type(None) in get_args(annotation):
        return _json_schema(
            next(item for item in get_args(annotation) if item is not type(None))
        )
    return {"type": "object"}
