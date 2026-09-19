// The six verbs a call can end in. Colours are categorical identity, not
// status: a refusal with the right reason scores exactly like a booking, so
// painting NO_ACTION red would lie about what the agent did.
// Validated as a set against the card surface (#0f172a) in dark mode.
export const OUTCOMES = {
  BOOK: { label: "Reserva", color: "#3987e5" },
  RESCHEDULE: { label: "Cambio", color: "#d95926" },
  CANCEL: { label: "Cancelación", color: "#199e70" },
  REGISTER: { label: "Alta", color: "#c98500" },
  NO_ACTION: { label: "Sin acción", color: "#d55181" },
  ESCALATE: { label: "Escalada", color: "#9085e9" },
};

// An absence of outcome is not an identity, so it takes recessive ink.
export const UNRECORDED = { label: "Sin registrar", color: "#475569" };

export function outcomeStyle(outcome) {
  return OUTCOMES[outcome] ?? UNRECORDED;
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
