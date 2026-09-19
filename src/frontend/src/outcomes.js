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
  BOOK: { label: "Booked", group: "wrote", chartColor: "#199e70" },
  RESCHEDULE: { label: "Rescheduled", group: "wrote", chartColor: "#3979c3" },
  CANCEL: { label: "Cancelled", group: "wrote", chartColor: "#d28434" },
  REGISTER: { label: "Registered", group: "wrote", chartColor: "#30a4ad" },
  NO_ACTION: { label: "No action", group: "closed", chartColor: "#94a3b8" },
  ESCALATE: { label: "Escalated", group: "closed", chartColor: "#9085e9" },
};

export const HISTORY_RANGES = [
  { value: "7", label: "Last 7 days" },
  { value: "14", label: "Last 14 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "all", label: "All time" },
  { value: "custom", label: "Custom dates" },
];

export function historyDateRange(range, now = new Date()) {
  if (range === "all") return { date_from: "", date_to: "" };
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Madrid", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(now).map(({ type, value }) => [type, value]),
  );
  const date_to = `${parts.year}-${parts.month}-${parts.day}`;
  const start = new Date(`${date_to}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - Number(range) + 1);
  return { date_from: start.toISOString().slice(0, 10), date_to };
}

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
