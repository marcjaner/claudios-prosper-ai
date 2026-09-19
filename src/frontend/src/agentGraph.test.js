import assert from "node:assert/strict";
import test from "node:test";

import { buildAgentActions, getActionLabel, getActionReason } from "./agentGraph.js";
import { REASONS } from "./outcomes.js";

function call(name, ts, payload = {}) {
  return { kind: "tool_call", ts, payload: { name, arguments: {}, ...payload } };
}

function result(name, ts, payload = {}) {
  return { kind: "tool_result", ts, payload: { name, status: 200, ...payload } };
}

function completed(name, ts, response = {}) {
  const tool_call_id = `${name}-${ts}`;
  return [
    call(name, ts, { tool_call_id }),
    result(name, ts + 0.25, { tool_call_id, response }),
  ];
}

const SUCCESS_TITLES = {
  search_patients: "Se ha buscado al paciente",
  register_patient: "Se ha registrado un nuevo paciente",
  get_clinic_catalogue: "Se han consultado los servicios del centro",
  search_availability: "Se ha consultado la disponibilidad de citas",
  get_patient_appointments: "Se han consultado las citas del paciente",
  book_appointment: "Se ha reservado una cita",
  reschedule_appointment: "Se ha cambiado la cita",
  cancel_appointment: "Se ha cancelado la cita",
  prepare_booking: "Reserva preparada, pendiente de confirmación",
  prepare_reschedule: "Cambio preparado, pendiente de confirmación",
  prepare_cancellation: "Cancelación preparada, pendiente de confirmación",
  confirm_action: "Se ha confirmado la acción solicitada",
  get_call_state: "Se ha consultado el estado de la solicitud",
  revise_request: "Se ha reabierto la solicitud",
  submit_no_action: "No se ha realizado ninguna acción",
  escalate_to_human: "Se ha solicitado la intervención del personal del centro",
};

test("all clinical actions use readable Spanish instead of tool names", () => {
  for (const [name, title] of Object.entries(SUCCESS_TITLES)) {
    const [action] = buildAgentActions(completed(name, 1));
    assert.equal(action.title, title);
    assert.equal(action.state, "success");
    assert.doesNotMatch(action.title + action.description + getActionLabel(name), /_/);
  }
});

test("confirmed actions show the actual evidence-backed outcome", () => {
  for (const [action, title] of Object.entries({
    BOOK: "Se ha reservado una cita",
    RESCHEDULE: "Se ha cambiado la cita",
    CANCEL: "Se ha cancelado una cita",
  })) {
    const [entry] = buildAgentActions(completed("confirm_action", 1, { action }));
    assert.equal(entry.title, title);
  }
});

test("lists only actual executions, in chronological order", () => {
  const actions = buildAgentActions([
    ...completed("search_patients", 1, { matches: [{ patient_id: "P1" }] }),
    ...completed("book_appointment", 2),
    { kind: "submit", ts: 3, payload: { route: "/api/v1/submit/book", status: 200 } },
    { kind: "tts", ts: 4, payload: { text: "Cita reservada" } },
    { kind: "stage_entered", ts: 5, payload: { stage: "identify" } },
    { kind: "fact_recorded", ts: 6, payload: { key: "patient_id", value: "P1", stage: "identify" } },
    { kind: "transition_rejected", ts: 7, payload: { requested: "execute", reason: "missing facts" } },
  ]);
  assert.deepEqual(actions.map(({ name }) => name), ["search_patients", "book_appointment"]);
  assert.deepEqual(actions.map(({ order }) => order), [1, 2]);
  assert.equal(actions[0].durationMs, 250);
});

test("a successful retry does not hide an earlier failed attempt or claim it succeeded", () => {
  const actions = buildAgentActions([
    call("book_appointment", 1, { tool_call_id: "first" }),
    result("book_appointment", 2, { tool_call_id: "first", status: 422, error: "Slot unavailable", ms: 14 }),
    ...completed("book_appointment", 3),
  ]);
  assert.equal(actions.length, 2);
  assert.deepEqual(actions.map(({ state }) => state), ["error", "success"]);
  assert.equal(actions[0].title, "Reserva de una cita");
  assert.notEqual(actions[0].title, SUCCESS_TITLES.book_appointment);
  assert.equal(actions[1].title, SUCCESS_TITLES.book_appointment);
  assert.equal(actions[0].durationMs, 14);
  assert.doesNotMatch(actions[0].description, /Slot unavailable/);
});

test("no-action and escalation remain separate real actions with understandable reasons", () => {
  const actions = buildAgentActions([
    call("submit_no_action", 1, { arguments: { reason: "no_availability" } }),
    result("submit_no_action", 2),
    call("escalate_to_human", 3, { arguments: { reason: "medical_emergency" } }),
    result("escalate_to_human", 4),
  ]);
  assert.equal(actions[0].title, SUCCESS_TITLES.submit_no_action);
  assert.equal(actions[0].description, "No hay citas disponibles para la solicitud.");
  assert.match(actions[1].description, /posible urgencia médica/);
  assert.doesNotMatch(actions[1].title, /transferido|transferida/);
});

test("all supported reasons are translated without showing internal codes", () => {
  for (const reason of REASONS) {
    const label = getActionReason(reason);
    assert.ok(label.length > 10);
    assert.notEqual(label, "Motivo no especificado.");
    assert.doesNotMatch(label, /_/);
  }
  assert.equal(getActionReason("unexpected_internal_reason"), "Motivo no especificado.");
});

test("empty history is not interpreted as an explicit no-action decision", () => {
  assert.deepEqual(buildAgentActions([]), []);
  assert.deepEqual(buildAgentActions([{ kind: "submit", ts: 1, payload: { route: "/api/v1/submit/no-action" } }]), []);
});

test("patient searches distinguish no matches and ambiguous matches from identification", () => {
  const actions = buildAgentActions([
    ...completed("search_patients", 1, { matches: [] }),
    ...completed("search_patients", 2, { matches: [{ patient_id: "P1" }, { patient_id: "P2" }] }),
  ]);
  assert.match(actions[0].description, /No se han encontrado coincidencias/);
  assert.match(actions[1].description, /2 posibles coincidencias/);
  assert.doesNotMatch(actions.map(({ title }) => title).join(" "), /identificado/);
});

test("availability and appointment lookups describe returned results, not new bookings", () => {
  const actions = buildAgentActions([
    ...completed("search_availability", 1, { slots: [] }),
    ...completed("search_availability", 2, { slots: [{ slot: "2026-09-24T10:30:00+02:00" }] }),
    ...completed("get_patient_appointments", 3, { appointments: [{ appointment_id: "A1" }] }),
    ...completed("get_patient_appointments", 4, { appointments: [] }),
  ]);
  assert.match(actions[0].description, /No se han encontrado citas disponibles/);
  assert.match(actions[1].description, /1 horario disponible/);
  assert.match(actions[2].description, /1 cita registrada/);
  assert.match(actions[3].description, /No se han encontrado citas registradas/);
  assert.ok(actions.every(({ title }) => title !== SUCCESS_TITLES.book_appointment));
});

test("a confirmed booking shows its recorded date in the clinic timezone", () => {
  const [action] = buildAgentActions([
    call("book_appointment", 1, { arguments: { slot: "2026-09-24T08:30:00Z" } }),
    result("book_appointment", 2),
  ]);
  assert.match(action.description, /24 de septiembre de 2026/);
  assert.match(action.description, /10:30/);
  assert.match(action.description, /hora peninsular/);
});

test("invalid or timezone-less dates do not produce fabricated appointment times", () => {
  for (const slot of ["invalid", "2026-09-24T10:30:00", null]) {
    const [action] = buildAgentActions([call("reschedule_appointment", 1, { arguments: { slot } }), result("reschedule_appointment", 2)]);
    assert.equal(action.description, "Se ha registrado el cambio de la cita solicitada.");
  }
});

test("correlates concurrent executions by id rather than response order", () => {
  const actions = buildAgentActions([
    call("search_patients", 1, { tool_call_id: "one", arguments: { name: "One" } }),
    call("search_patients", 2, { tool_call_id: "two", arguments: { name: "Two" } }),
    result("search_patients", 3, { tool_call_id: "two", response: { matches: [] }, ms: 0 }),
    result("search_patients", 4, { tool_call_id: "one", status: 500 }),
  ]);
  assert.equal(actions[0].state, "error");
  assert.equal(actions[1].state, "success");
  assert.equal(actions[1].durationMs, 0);
  assert.deepEqual(actions[1].arguments, { name: "Two" });
});

test("supports legacy names, requests and FIFO pairing without ids", () => {
  const actions = buildAgentActions([
    call("search_patient", 1, { arguments: undefined, request: { name: "Legacy" } }),
    call("search_patients", 2),
    result("search_patients", 3, { response: { matches: [{ patient_id: "P1" }] } }),
    result("search_patient", 4, { status: 404 }),
    ...completed("search_availability", 5, { slots: 14 }),
  ]);
  assert.equal(actions[0].title, SUCCESS_TITLES.search_patients);
  assert.deepEqual(actions[0].arguments, { name: "Legacy" });
  assert.deepEqual(actions.map(({ state }) => state), ["success", "error", "success"]);
  assert.match(actions[2].description, /14 horarios disponibles/);
  assert.equal(getActionLabel("search_patient"), getActionLabel("search_patients"));
});

test("unfinished actions are pending while live and unconfirmed when the call has ended", () => {
  const events = [call("cancel_appointment", 1)];
  const [live] = buildAgentActions(events, { live: true });
  const [ended] = buildAgentActions(events);
  assert.equal(live.state, "pending");
  assert.equal(ended.state, "missing");
  assert.equal(ended.durationMs, null);
  assert.equal(live.title, "Cancelación de una cita");
  assert.equal(ended.title, "Cancelación de una cita");
  assert.match(ended.description, /No consta confirmación/);
});

test("orphan responses retain results without inventing start time, arguments or duration", () => {
  const [action] = buildAgentActions([result("book_appointment", 4)]);
  assert.equal(action.startedAt, null);
  assert.equal(action.arguments, null);
  assert.equal(action.durationMs, null);
  assert.equal(action.resultAt, 4);
  assert.equal(action.title, SUCCESS_TITLES.book_appointment);
});

test("unconfirmed and failed responses never receive a completed-action title", () => {
  const actions = buildAgentActions([
    result("submit_no_action", 1, { status: undefined }),
    result("submit_no_action", 2, { status: 200, error: "Failed" }),
  ]);
  assert.deepEqual(actions.map(({ state }) => state), ["recorded", "error"]);
  assert.ok(actions.every(({ title }) => title !== SUCCESS_TITLES.submit_no_action));
});

test("unknown actions remain visible with a neutral label, not a fabricated clinical outcome", () => {
  const [action] = buildAgentActions(completed("other_internal_tool", 1));
  assert.equal(action.title, "Otra gestión del asistente");
  assert.doesNotMatch(action.title + action.description, /other_internal_tool/);
  assert.equal(getActionLabel("other_internal_tool"), "Otra gestión del asistente");
});

test("sorts timestamps stably without mutating incoming events", () => {
  const events = [
    result("search_patients", 3, { tool_call_id: "one", ms: -1 }),
    call("search_patients", 1, { tool_call_id: "one" }),
    call("get_clinic_catalogue", 1),
  ];
  const original = structuredClone(events);
  const actions = buildAgentActions(events);
  assert.deepEqual(events, original);
  assert.deepEqual(actions.map(({ name }) => name), ["search_patients", "get_clinic_catalogue"]);
  assert.equal(actions[0].durationMs, 2000);
});
