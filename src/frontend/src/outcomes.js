// A call either changed the clinic's diary, closed with a reasoned record, or
// left nothing behind. Only the last always fails the case: NO_ACTION with the
// right reason scores exactly like a booking, and problems 6 and 14 require it.
// So colour carries the group and the verb is always spelled out in text.
// Validated as a set against the card surface (#0f172a) in dark mode.
export const GROUPS = {
  wrote: { label: "Acción sobre la agenda", color: "#199e70" },
  closed: { label: "Cerrada sin escribir", color: "#9085e9" },
  absent: { label: "Sin registro", color: "#e66767" },
};

export const OUTCOMES = {
  BOOK: { label: "Reserva", group: "wrote" },
  RESCHEDULE: { label: "Cambio", group: "wrote" },
  CANCEL: { label: "Cancelación", group: "wrote" },
  REGISTER: { label: "Alta", group: "wrote" },
  NO_ACTION: { label: "Sin acción", group: "closed" },
  ESCALATE: { label: "Escalada", group: "closed" },
};

export function outcomeStyle(outcome) {
  const entry = OUTCOMES[outcome];
  if (!entry) return { label: "Sin registro", color: GROUPS.absent.color };
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
