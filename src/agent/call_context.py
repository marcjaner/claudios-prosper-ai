"""Request-scoped scheduling evidence for one live call."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal

RequestKind = Literal["book", "reschedule", "cancel"]
RequestStatus = Literal["collecting", "options_ready", "proposed", "completed"]
ProposalStatus = Literal["prepared", "submitting", "accepted", "rejected", "uncertain"]


@dataclass(frozen=True)
class SlotEvidence:
    slot_id: str
    provider_id: str
    location_id: str
    appointment_type_id: str
    start_time: str
    eligible_policy_ids: tuple[str, ...]
    specialty_id: str | None = None


@dataclass(frozen=True)
class AppointmentEvidence:
    appointment_ref: str
    appointment_id: str
    patient_id: str
    provider_id: str
    specialty_id: str | None = None


@dataclass
class RequestDraft:
    request_id: str
    patient_id: str
    kind: RequestKind | None = None
    revision: int = 1
    criteria: dict[str, Any] = field(default_factory=dict)
    slots: dict[str, SlotEvidence] = field(default_factory=dict)
    appointments: dict[str, AppointmentEvidence] = field(default_factory=dict)
    status: RequestStatus = "collecting"


@dataclass
class ActionProposal:
    proposal_id: str
    request_id: str
    request_revision: int
    action: Literal["BOOK", "RESCHEDULE", "CANCEL"]
    payload: dict[str, Any]
    prepared_turn: int
    status: ProposalStatus = "prepared"
    result: dict[str, Any] | None = None


@dataclass
class CallContext:
    call_id: str
    turn_number: int = 0
    active_request_id: str | None = None
    requests: dict[str, RequestDraft] = field(default_factory=dict)
    proposals: dict[str, ActionProposal] = field(default_factory=dict)
    verified_patient_ids: set[str] = field(default_factory=set)
    _request_sequence: int = 0
    _slot_sequence: int = 0
    _appointment_sequence: int = 0
    _proposal_sequence: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def begin_turn(self) -> int:
        with self._lock:
            self.turn_number += 1
            return self.turn_number

    def record_patient_lookup(self, response: dict[str, Any]) -> None:
        matches = response.get("matches", [])
        patient_ids = [
            match.get("patient_id")
            for match in matches
            if isinstance(match, dict) and isinstance(match.get("patient_id"), str)
        ]
        if len(patient_ids) == 1:
            self.verified_patient_ids.add(patient_ids[0])

    def require_verified_patient(self, patient_id: str) -> None:
        if patient_id not in self.verified_patient_ids:
            raise ValueError(
                "patient_not_verified: use search_patients until exactly one patient matches"
            )

    def select_request(
        self,
        patient_id: str,
        *,
        request_id: str | None = None,
        new_request: bool = False,
    ) -> RequestDraft:
        self.require_verified_patient(patient_id)
        if request_id and new_request:
            raise ValueError("choose request_id or new_request, not both")
        if request_id:
            request = self._request(request_id)
            if request.patient_id != patient_id:
                raise ValueError("request_patient_mismatch")
            if request.status == "completed":
                raise ValueError("request_already_completed")
            self.active_request_id = request_id
            return request
        if new_request:
            return self._create_request(patient_id)

        matches = [
            request
            for request in self.requests.values()
            if request.patient_id == patient_id and request.status != "completed"
        ]
        if len(matches) > 1:
            raise ValueError("request_id_required: several unfinished requests match")
        if matches:
            self.active_request_id = matches[0].request_id
            return matches[0]
        return self._create_request(patient_id)

    def install_slots(
        self,
        request: RequestDraft,
        criteria: dict[str, Any],
        slots: list[dict[str, Any]],
        *,
        expected_revision: int,
    ) -> list[dict[str, Any]]:
        if request.revision != expected_revision:
            raise ValueError("request_changed_during_availability_search")
        self._ensure_replaceable(request)
        self._invalidate_proposals(request.request_id, {"BOOK", "RESCHEDULE"})
        if request.slots or request.criteria:
            request.revision += 1
        request.criteria = dict(criteria)
        request.slots.clear()
        exposed: list[dict[str, Any]] = []
        for raw_slot in slots:
            slot = self._slot_evidence(raw_slot)
            request.slots[slot.slot_id] = slot
            exposed.append({**raw_slot, "slot_id": slot.slot_id})
        request.status = (
            "options_ready" if request.slots or request.appointments else "collecting"
        )
        return exposed

    def install_appointments(
        self,
        request: RequestDraft,
        appointments: list[dict[str, Any]],
        *,
        expected_revision: int,
    ) -> list[dict[str, Any]]:
        if request.revision != expected_revision:
            raise ValueError("request_changed_during_appointment_lookup")
        self._ensure_replaceable(request)
        self._invalidate_proposals(request.request_id, {"CANCEL", "RESCHEDULE"})
        if request.appointments:
            request.revision += 1
        request.appointments.clear()
        exposed: list[dict[str, Any]] = []
        for raw_appointment in appointments:
            if raw_appointment.get("patient_id") != request.patient_id:
                raise ValueError("appointment_patient_mismatch")
            appointment = self._appointment_evidence(raw_appointment)
            request.appointments[appointment.appointment_ref] = appointment
            public = dict(raw_appointment)
            public.pop("appointment_id", None)
            exposed.append({**public, "appointment_ref": appointment.appointment_ref})
        request.status = (
            "options_ready" if request.appointments or request.slots else "collecting"
        )
        return exposed

    def prepare_booking(self, slot_id: str, policy_id: str) -> ActionProposal:
        request, slot = self._find_slot(slot_id)
        self._require_policy(slot, policy_id)
        payload = {
            "call_id": self.call_id,
            "patient_id": request.patient_id,
            "provider_id": slot.provider_id,
            "location_id": slot.location_id,
            "appointment_type_id": slot.appointment_type_id,
            "slot": slot.start_time,
            "policy_id": policy_id,
        }
        return self._prepare(request, "BOOK", payload)

    def prepare_reschedule(
        self, appointment_ref: str, slot_id: str, policy_id: str
    ) -> ActionProposal:
        appointment_request, appointment = self._find_appointment(appointment_ref)
        slot_request, slot = self._find_slot(slot_id)
        if appointment_request.request_id != slot_request.request_id:
            raise ValueError("cross_request_evidence")
        self._require_policy(slot, policy_id)
        payload = {
            "call_id": self.call_id,
            "appointment_id": appointment.appointment_id,
            "provider_id": slot.provider_id,
            "location_id": slot.location_id,
            "slot": slot.start_time,
            "policy_id": policy_id,
        }
        return self._prepare(appointment_request, "RESCHEDULE", payload)

    def prepare_cancellation(self, appointment_ref: str) -> ActionProposal:
        request, appointment = self._find_appointment(appointment_ref)
        payload = {
            "call_id": self.call_id,
            "appointment_id": appointment.appointment_id,
        }
        return self._prepare(request, "CANCEL", payload)

    def begin_submission(self, proposal_id: str, confirmed: bool) -> ActionProposal:
        with self._lock:
            proposal = self._proposal(proposal_id)
            request = self._request(proposal.request_id)
            if proposal.status != "prepared":
                raise ValueError(f"proposal_not_confirmable: {proposal.status}")
            if not confirmed:
                proposal.status = "rejected"
                raise ValueError("action_not_confirmed")
            if proposal.prepared_turn >= self.turn_number:
                raise ValueError("confirmation_requires_new_caller_turn")
            if proposal.request_revision != request.revision:
                proposal.status = "rejected"
                raise ValueError("stale_proposal")
            if request.status == "completed":
                raise ValueError("request_already_completed")
            self._validate_proposal_evidence(request, proposal)
            proposal.status = "submitting"
            return proposal

    def accept_submission(self, proposal_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            proposal = self._proposal(proposal_id)
            if proposal.status != "submitting":
                return
            proposal.status = "accepted"
            proposal.result = result
            self._request(proposal.request_id).status = "completed"

    def reject_submission(self, proposal_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            proposal = self._proposal(proposal_id)
            if proposal.status == "submitting":
                proposal.status = "rejected"
                proposal.result = result

    def mark_submission_uncertain(self, proposal_id: str) -> None:
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if proposal and proposal.status == "submitting":
                proposal.status = "uncertain"

    def mark_any_submission_uncertain(self) -> None:
        with self._lock:
            for proposal in self.proposals.values():
                if proposal.status == "submitting":
                    proposal.status = "uncertain"

    def revise_request(self, request_id: str) -> RequestDraft:
        request = self._request(request_id)
        self._ensure_replaceable(request)
        self._invalidate_proposals(request_id)
        request.revision += 1
        request.slots.clear()
        request.status = "collecting"
        self.active_request_id = request_id
        return request

    def has_prepared_proposal(self) -> bool:
        return any(
            proposal.status == "prepared" for proposal in self.proposals.values()
        )

    def projection(self) -> dict[str, Any]:
        return {
            "stage": "request_context",
            "turn_number": self.turn_number,
            "active_request_id": self.active_request_id,
            "requests": [
                {
                    "request_id": request.request_id,
                    "patient_id": request.patient_id,
                    "kind": request.kind,
                    "revision": request.revision,
                    "status": request.status,
                    "slot_ids": list(request.slots),
                    "appointment_refs": list(request.appointments),
                }
                for request in self.requests.values()
            ],
            "proposals": [
                {
                    "proposal_id": proposal.proposal_id,
                    "request_id": proposal.request_id,
                    "action": proposal.action,
                    "status": proposal.status,
                    "prepared_turn": proposal.prepared_turn,
                }
                for proposal in self.proposals.values()
            ],
        }

    def _create_request(self, patient_id: str) -> RequestDraft:
        self._request_sequence += 1
        request = RequestDraft(
            request_id=f"req_{self._request_sequence}", patient_id=patient_id
        )
        self.requests[request.request_id] = request
        self.active_request_id = request.request_id
        return request

    def _slot_evidence(self, slot: dict[str, Any]) -> SlotEvidence:
        required = (
            "provider_id",
            "location_id",
            "appointment_type_id",
            "start_time",
        )
        if any(not isinstance(slot.get(field), str) for field in required):
            raise ValueError("invalid_slot_evidence")
        policies = slot.get("payable_with", slot.get("eligible_policy_ids", []))
        if not isinstance(policies, list) or not all(
            isinstance(policy, str) for policy in policies
        ):
            raise ValueError("invalid_slot_policy_evidence")
        self._slot_sequence += 1
        return SlotEvidence(
            slot_id=f"slot_{self._slot_sequence}",
            provider_id=slot["provider_id"],
            location_id=slot["location_id"],
            appointment_type_id=slot["appointment_type_id"],
            start_time=slot["start_time"],
            eligible_policy_ids=tuple(policies),
            specialty_id=slot.get("specialty_id"),
        )

    def _appointment_evidence(self, appointment: dict[str, Any]) -> AppointmentEvidence:
        required = ("appointment_id", "patient_id", "provider_id")
        if any(not isinstance(appointment.get(field), str) for field in required):
            raise ValueError("invalid_appointment_evidence")
        self._appointment_sequence += 1
        return AppointmentEvidence(
            appointment_ref=f"appt_{self._appointment_sequence}",
            appointment_id=appointment["appointment_id"],
            patient_id=appointment["patient_id"],
            provider_id=appointment["provider_id"],
            specialty_id=appointment.get("specialty_id"),
        )

    def _prepare(
        self,
        request: RequestDraft,
        action: Literal["BOOK", "RESCHEDULE", "CANCEL"],
        payload: dict[str, Any],
    ) -> ActionProposal:
        self._ensure_replaceable(request)
        for proposal in self.proposals.values():
            if (
                proposal.request_id == request.request_id
                and proposal.status == "prepared"
                and proposal.action == action
                and proposal.payload == payload
            ):
                return proposal
        self._invalidate_proposals(request.request_id)
        self._proposal_sequence += 1
        proposal = ActionProposal(
            proposal_id=f"prop_{self._proposal_sequence}",
            request_id=request.request_id,
            request_revision=request.revision,
            action=action,
            payload=dict(payload),
            prepared_turn=self.turn_number,
        )
        self.proposals[proposal.proposal_id] = proposal
        if action == "BOOK":
            request.kind = "book"
        elif action == "RESCHEDULE":
            request.kind = "reschedule"
        else:
            request.kind = "cancel"
        request.status = "proposed"
        return proposal

    def _invalidate_proposals(
        self,
        request_id: str,
        actions: set[str] | None = None,
    ) -> None:
        proposals = [
            proposal
            for proposal in self.proposals.values()
            if proposal.request_id == request_id
            and (actions is None or proposal.action in actions)
        ]
        if any(
            proposal.status in {"submitting", "uncertain"} for proposal in proposals
        ):
            raise ValueError("request_has_uncertain_submission")
        for proposal in proposals:
            if proposal.status == "prepared":
                proposal.status = "rejected"

    def _ensure_replaceable(self, request: RequestDraft) -> None:
        if request.status == "completed":
            raise ValueError("request_already_completed")
        if any(
            proposal.request_id == request.request_id
            and proposal.status in {"submitting", "uncertain"}
            for proposal in self.proposals.values()
        ):
            raise ValueError("request_has_uncertain_submission")

    def _find_slot(self, slot_id: str) -> tuple[RequestDraft, SlotEvidence]:
        matches = [
            (request, request.slots[slot_id])
            for request in self.requests.values()
            if slot_id in request.slots
        ]
        if len(matches) != 1:
            raise ValueError("slot_not_found_or_stale")
        return matches[0]

    def _find_appointment(
        self, appointment_ref: str
    ) -> tuple[RequestDraft, AppointmentEvidence]:
        matches = [
            (request, request.appointments[appointment_ref])
            for request in self.requests.values()
            if appointment_ref in request.appointments
        ]
        if len(matches) != 1:
            raise ValueError("appointment_not_found_or_stale")
        return matches[0]

    def _validate_proposal_evidence(
        self, request: RequestDraft, proposal: ActionProposal
    ) -> None:
        if proposal.action == "BOOK":
            patient_id = proposal.payload["patient_id"]
            if patient_id != request.patient_id:
                raise ValueError("stale_proposal_evidence")
            self._matching_slot(request, proposal.payload)
            return
        appointment_id = proposal.payload["appointment_id"]
        if not any(
            appointment.appointment_id == appointment_id
            for appointment in request.appointments.values()
        ):
            raise ValueError("stale_proposal_evidence")
        if proposal.action == "RESCHEDULE":
            self._matching_slot(request, proposal.payload)

    @staticmethod
    def _matching_slot(request: RequestDraft, payload: dict[str, Any]) -> None:
        if not any(
            slot.provider_id == payload["provider_id"]
            and slot.location_id == payload["location_id"]
            and slot.start_time == payload["slot"]
            and payload["policy_id"] in slot.eligible_policy_ids
            and (
                "appointment_type_id" not in payload
                or slot.appointment_type_id == payload["appointment_type_id"]
            )
            for slot in request.slots.values()
        ):
            raise ValueError("stale_proposal_evidence")

    @staticmethod
    def _require_policy(slot: SlotEvidence, policy_id: str) -> None:
        if policy_id not in slot.eligible_policy_ids:
            raise ValueError("policy_not_eligible_for_slot")

    def _request(self, request_id: str) -> RequestDraft:
        request = self.requests.get(request_id)
        if request is None:
            raise ValueError("request_not_found")
        return request

    def _proposal(self, proposal_id: str) -> ActionProposal:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise ValueError("proposal_not_found")
        return proposal
