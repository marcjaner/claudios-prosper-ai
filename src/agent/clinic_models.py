"""Payloads sent to Prosper's submission endpoints."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel


class OutcomeReason(StrEnum):
    NOT_ELIGIBLE_AGE = "not_eligible_age"
    REFERRAL_REQUIRED = "referral_required"
    PROVIDER_NOT_IN_NETWORK = "provider_not_in_network"
    SPECIALTY_NOT_COVERED = "specialty_not_covered"
    LOCATION_NOT_COVERED = "location_not_covered"
    INSURER_REFERRAL_REQUIRED = "insurer_referral_required"
    ALLOWANCE_EXHAUSTED = "allowance_exhausted"
    PROVIDER_ON_LEAVE = "provider_on_leave"
    LOCATION_HOURS = "location_hours"
    TYPE_NOT_OFFERED = "type_not_offered"
    PATIENT_HISTORY = "patient_history"
    NO_AVAILABILITY = "no_availability"
    CLINIC_CLOSED = "clinic_closed"
    PATIENT_NOT_FOUND = "patient_not_found"
    PROVIDER_NOT_FOUND = "provider_not_found"
    CALLER_NOT_AUTHORISED = "caller_not_authorised"
    OUT_OF_SCOPE = "out_of_scope"
    MEDICAL_EMERGENCY = "medical_emergency"


class RegisterPatientRequest(BaseModel):
    call_id: str
    given_name: str
    first_surname: str
    second_surname: str
    national_id: str
    date_of_birth: date
    phone: str
    email: str
    insurer: str


class BookRequest(BaseModel):
    call_id: str
    patient_id: str
    provider_id: str
    location_id: str
    appointment_type_id: str
    slot: str
    policy_id: str


class RescheduleRequest(BaseModel):
    call_id: str
    appointment_id: str
    provider_id: str
    location_id: str
    slot: str
    policy_id: str


class CancelRequest(BaseModel):
    call_id: str
    appointment_id: str


class OutcomeRequest(BaseModel):
    call_id: str
    reason: OutcomeReason
