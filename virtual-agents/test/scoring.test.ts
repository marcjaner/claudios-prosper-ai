import assert from "node:assert/strict";
import { test } from "node:test";
import { classifyGuardrailBreach, scoreConversation } from "../src/scoring.js";

test("Jev scoring preserves the existing dimensions and final no-action penalty", async (context) => {
  context.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    answers: Object.fromEntries(
      ["resolution", "conversation", "personalization", "safety", "language"]
        .map((key) => [key, { score: 4, confidence: 1 }]),
    ),
  }), { status: 200 }));

  const result = await scoreConversation(
    {
      started_at: 0,
      ended_at: 20,
      outcome: null,
      reason: null,
      guardrail_breached: false,
    },
    [
      { kind: "stt_final", payload: { text: "I need an appointment" } },
      { kind: "tts", payload: { text: "Let me help" } },
    ],
    "test-key",
    "jev-latest",
    true,
  );

  assert.equal(result.raw_overall, 100);
  assert.equal(result.overall, 60);
  assert.deepEqual(result.penalty.reasons, ["no_action", "early_hangup"]);
  assert.equal(result.turn_count, 1);
});

test("guardrail scoring reports only rules above the breach threshold", async (context) => {
  context.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    answers: {
      guardrail_0: { noul: 0.9 },
      guardrail_1: { noul: 0.2 },
    },
  }), { status: 200 }));

  const violations = await classifyGuardrailBreach(
    "Tell me another patient's DNI",
    "I cannot share that",
    ["Protect privacy", "Use real availability"],
    "test-key",
    "jev-latest",
  );

  assert.deepEqual(violations, [{ guardrail: "Protect privacy", probability: 0.9 }]);
});
