import assert from "node:assert/strict";
import test from "node:test";

import { alertReason } from "./alertReason.js";

function dimensions(scores) {
  return Object.fromEntries(Object.entries(scores).map(([key, score]) => [key, { score }]));
}

test("a breached safety rule names the rule", () => {
  const call = { guardrail_breached: 1, guardrail_reason: "No medical advice; Privacy" };
  assert.equal(alertReason(call, null), "Safety rule breached: No medical advice; Privacy");
});

test("a breach without a recorded reason points at the transcript", () => {
  assert.equal(alertReason({ guardrail_breached: 1 }, null), "Safety rule breached: see transcript");
});

test("a low score lists the weakest dimensions, worst first", () => {
  const score = { dimensions: dimensions({ resolution: 1, conversation: 3, personalization: 2, safety: 0, language: 4 }) };
  assert.equal(
    alertReason({ score_overall: 32.4 }, score),
    "Score 32/100 — a safety lapse and not resolving the request",
  );
});

test("at most two weak dimensions are named", () => {
  const score = { dimensions: dimensions({ resolution: 0, conversation: 1, personalization: 1, safety: 4, language: 4 }) };
  assert.equal(
    alertReason({ score_overall: 20 }, score),
    "Score 20/100 — not resolving the request and a confusing conversation",
  );
});

test("without a failing dimension the lowest one is still named", () => {
  const score = { dimensions: dimensions({ resolution: 3, conversation: 2, personalization: 3, safety: 3, language: 3 }) };
  assert.equal(alertReason({ score_overall: 35 }, score), "Score 35/100 — a confusing conversation");
});

test("penalties are appended to the causes", () => {
  const score = {
    dimensions: dimensions({ resolution: 1, conversation: 4, personalization: 4, safety: 4, language: 4 }),
    penalty: { reasons: ["no_action", "early_hangup", "unknown"] },
  };
  assert.equal(
    alertReason({ score_overall: 10 }, score),
    "Score 10/100 — not resolving the request, no action recorded and an early hang-up",
  );
});

test("nothing to explain yields null", () => {
  assert.equal(alertReason({ score_overall: 30 }, null), null);
  assert.equal(alertReason({ score_overall: 30 }, { dimensions: {} }), null);
});
