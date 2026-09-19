// Jev scores each dimension 0..4; anything at or below this reads as a failure.
const WEAK_DIMENSION_SCORE = 1;
const MAX_WEAK_DIMENSIONS = 2;

const DIMENSION_LABELS = {
  resolution: "not resolving the request",
  conversation: "a confusing conversation",
  personalization: "re-asking known details",
  safety: "a safety lapse",
  language: "language problems",
};

const PENALTY_LABELS = {
  no_action: "no action recorded",
  early_hangup: "an early hang-up",
};

function joinList(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

function weakDimensions(dimensions) {
  const ranked = Object.entries(dimensions ?? {})
    .filter(([key, value]) => key in DIMENSION_LABELS && typeof value?.score === "number")
    .sort(([, a], [, b]) => a.score - b.score);
  const weak = ranked.filter(([, value]) => value.score <= WEAK_DIMENSION_SCORE).slice(0, MAX_WEAK_DIMENSIONS);
  return (weak.length ? weak : ranked.slice(0, 1)).map(([key]) => DIMENSION_LABELS[key]);
}

/** One short line telling staff why the agent should stop, or null if there is nothing to say. */
export function alertReason(call, score) {
  if (call.guardrail_breached) {
    return `Safety rule breached: ${call.guardrail_reason || "see transcript"}`;
  }
  const causes = weakDimensions(score?.dimensions);
  if (causes.length === 0) return null;
  const penalties = (score?.penalty?.reasons ?? []).map((reason) => PENALTY_LABELS[reason]).filter(Boolean);
  return `Score ${Math.round(call.score_overall)}/100 — ${joinList([...causes, ...penalties])}`;
}
