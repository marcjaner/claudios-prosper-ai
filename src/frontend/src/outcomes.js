// A call either changed the clinic's diary, closed with a reasoned record, or
// left nothing behind. Only the last always fails the case: NO_ACTION with the
// right reason scores exactly like a booking, and problems 6 and 14 require it.
// So colour carries the group and the verb is always spelled out in text.
// Validated as a set against the card surface (#0f172a) in dark mode.
export const GROUPS = {
  wrote: { label: "Schedule updated", color: "#199e70" },
  closed: { label: "Closed without changes", color: "#9085e9" },
  absent: { label: "No record", color: "#e66767" },
};

export const OUTCOMES = {
  BOOK: { label: "Booked", group: "wrote" },
  RESCHEDULE: { label: "Rescheduled", group: "wrote" },
  CANCEL: { label: "Cancelled", group: "wrote" },
  REGISTER: { label: "Registered", group: "wrote" },
  NO_ACTION: { label: "No action", group: "closed" },
  ESCALATE: { label: "Escalated", group: "closed" },
};

export function outcomeStyle(outcome) {
  const entry = OUTCOMES[outcome];
  if (!entry) return { label: "No record", color: GROUPS.absent.color };
  return { label: entry.label, color: GROUPS[entry.group].color };
}

// The platform's closed vocabulary. The first eleven mirror the clinic's own
// published restrictions one for one.
export const REASONS = [
  "not_eligible_age",
  "referral_required",
  "provider_not_in_network",
  "specialty_not_covered",
  "location_not_covered",
  "insurer_referral_required",
  "allowance_exhausted",
  "provider_on_leave",
  "location_hours",
  "type_not_offered",
  "patient_history",
  "no_availability",
  "clinic_closed",
  "patient_not_found",
  "provider_not_found",
  "caller_not_authorised",
  "out_of_scope",
  "medical_emergency",
];

// The clinic's ten plans, exactly as the platform spells them.
export const INSURERS = [
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
];
