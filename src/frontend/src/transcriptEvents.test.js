import assert from "node:assert/strict";
import test from "node:test";

import { collapseTranscriptPartials } from "./transcriptEvents.js";

function event(kind, text) {
  return { kind, payload: { text } };
}

test("keeps only the latest interim while a caller turn is in progress", () => {
  const events = [
    event("stt_final", "First turn"),
    event("stt_partial", "I need"),
    event("stt_partial", "I need an appointment"),
  ];

  assert.deepEqual(collapseTranscriptPartials(events, true), [events[0], events[2]]);
});

test("replaces interim hypotheses with the completed caller turn", () => {
  const events = [
    event("stt_partial", "I need"),
    event("stt_partial", "I need an appointment"),
    event("stt_final", "I need an appointment tomorrow"),
  ];

  assert.deepEqual(collapseTranscriptPartials(events, true), [events[2]]);
});

test("does not show an unfinished interim after the call ends", () => {
  const events = [event("stt_final", "First turn"), event("stt_partial", "Actually")];

  assert.deepEqual(collapseTranscriptPartials(events, false), [events[0]]);
});
