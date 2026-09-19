const ACTION_COPY = {
  get_clinic: {
    label: "Consulta del centro",
    success: "Se han consultado los datos del centro",
    description: "Se han revisado los datos oficiales del centro.",
  },
  find_patient: {
    label: "Búsqueda del paciente",
    success: "Se ha verificado al paciente",
    description: "Se ha consultado y verificado la ficha del paciente.",
  },
  resolve_request: {
    label: "Resolución de la solicitud",
    success: "Se ha interpretado la solicitud",
    description: "Se ha resuelto la especialidad, profesional o necesidad indicada.",
  },
  list_appointments: {
    label: "Consulta de citas",
    success: "Se han consultado las citas del paciente",
    description: "Se han revisado las citas verificadas del paciente.",
  },
  prepare_action: {
    label: "Preparación de la acción",
    success: "Se ha preparado la acción para confirmar",
    description: "La acción se ha preparado sin enviarla todavía.",
  },
  confirm_action: {
    label: "Confirmación de la acción",
    success: "Se ha enviado la acción confirmada",
    description: "La acción aprobada por el paciente se ha enviado a Prosper.",
  },
  confirm_actions: {
    label: "Confirmación de acciones",
    success: "Se han enviado las acciones confirmadas",
    description: "Las acciones aprobadas por el paciente se han enviado a Prosper.",
  },
  report_outcome: {
    label: "Registro del resultado",
    success: "Se ha registrado el resultado de la llamada",
    description: "El rechazo o escalado se ha enviado a Prosper.",
  },
  search_patients: {
    label: "Búsqueda del paciente",
    success: "Se ha buscado al paciente",
    description: "Se ha consultado el registro de pacientes del centro.",
  },
  register_patient: {
    label: "Registro de un nuevo paciente",
    success: "Se ha registrado un nuevo paciente",
    description: "Se ha registrado el alta con los datos facilitados durante la llamada.",
  },
  get_clinic_catalogue: {
    label: "Consulta de los servicios del centro",
    success: "Se han consultado los servicios del centro",
    description: "Se han revisado las especialidades, los profesionales y las condiciones de atención del centro.",
  },
  search_availability: {
    label: "Búsqueda de citas disponibles",
    success: "Se ha consultado la disponibilidad de citas",
    description: "Se han consultado los horarios disponibles para la solicitud del paciente.",
  },
  get_patient_appointments: {
    label: "Consulta de las citas del paciente",
    success: "Se han consultado las citas del paciente",
    description: "Se han revisado las citas que figuran en el registro del paciente.",
  },
  book_appointment: {
    label: "Reserva de una cita",
    success: "Se ha reservado una cita",
    description: "Se ha registrado la reserva de la cita solicitada.",
  },
  reschedule_appointment: {
    label: "Cambio de una cita",
    success: "Se ha cambiado la cita",
    description: "Se ha registrado el cambio de la cita solicitada.",
  },
  cancel_appointment: {
    label: "Cancelación de una cita",
    success: "Se ha cancelado la cita",
    description: "Se ha registrado la cancelación de la cita solicitada.",
  },
  submit_no_action: {
    label: "Cierre de la solicitud sin cambios",
    success: "No se ha realizado ninguna acción",
    description: "Se ha registrado el cierre de la solicitud sin una nueva gestión de cita.",
  },
  escalate_to_human: {
    label: "Solicitud de atención por el personal del centro",
    success: "Se ha solicitado la intervención del personal del centro",
    description: "Se ha registrado una solicitud para que el personal del centro atienda el caso.",
  },
};

const OTHER_ACTION = {
  label: "Otra gestión del asistente",
  success: "Otra gestión del asistente",
  description: "No se dispone de un resumen de esta gestión.",
};

const REASON_LABELS = {
  not_eligible_age: "La edad no cumple los requisitos de esta cita.",
  referral_required: "Se requiere una derivación médica.",
  provider_not_in_network: "El profesional no está incluido en la cobertura del seguro.",
  specialty_not_covered: "El seguro no cubre la especialidad solicitada.",
  location_not_covered: "El seguro no cubre la atención en el centro solicitado.",
  insurer_referral_required: "El seguro requiere una derivación médica.",
  allowance_exhausted: "Se ha agotado la cobertura disponible para esta prestación.",
  provider_on_leave: "El profesional no está disponible.",
  location_hours: "El horario solicitado está fuera del horario del centro.",
  type_not_offered: "El centro no ofrece el tipo de cita solicitado.",
  patient_history: "No se cumplen los requisitos de antecedentes o visitas previas.",
  no_availability: "No hay citas disponibles para la solicitud.",
  clinic_closed: "El centro está cerrado en el horario solicitado.",
  patient_not_found: "No se ha encontrado al paciente en el registro.",
  provider_not_found: "No se ha encontrado al profesional solicitado.",
  caller_not_authorised: "La persona que llama no está autorizada para realizar esta gestión.",
  out_of_scope: "La solicitud requiere una gestión que el asistente no puede realizar.",
  medical_emergency: "Se ha comunicado una posible urgencia médica.",
};

const STATE_DESCRIPTIONS = {
  error: "La gestión no se ha podido confirmar por una incidencia.",
  pending: "El asistente está realizando esta gestión.",
  missing: "No consta confirmación de que esta gestión haya finalizado.",
  recorded: "Se ha registrado una respuesta, pero no consta si la gestión se completó.",
};

const appointmentFormat = new Intl.DateTimeFormat("es-ES", {
  dateStyle: "long",
  timeStyle: "short",
  timeZone: "Europe/Madrid",
});

function toolName(name) {
  return name === "search_patient" ? "search_patients" : (name ?? "tool sin nombre");
}

function actionCopy(name) {
  const normalized = toolName(name);
  return Object.hasOwn(ACTION_COPY, normalized) ? ACTION_COPY[normalized] : OTHER_ACTION;
}

export function getActionLabel(name) {
  return actionCopy(name).label;
}

export function getActionReason(reason) {
  return Object.hasOwn(REASON_LABELS, reason) ? REASON_LABELS[reason] : "Motivo no especificado.";
}

function eventKey(payload) {
  return payload.tool_call_id != null
    ? `id:${payload.tool_call_id}`
    : `name:${toolName(payload.name)}`;
}

function pairExecutions(events) {
  const pending = new Map();
  const executions = [];
  const ordered = events
    .filter(({ kind }) => kind === "tool_call" || kind === "tool_result")
    .slice()
    .sort((a, b) => a.ts - b.ts || (a.id != null && b.id != null ? a.id - b.id : 0));

  for (const event of ordered) {
    const payload = event.payload ?? {};
    const key = eventKey(payload);
    if (event.kind === "tool_call") {
      const execution = {
        name: toolName(payload.name),
        startedAt: Number.isFinite(event.ts) ? event.ts : null,
        arguments: payload.arguments ?? payload.request ?? null,
        resultAt: null,
        result: null,
      };
      const queue = pending.get(key) ?? [];
      queue.push(execution);
      pending.set(key, queue);
      executions.push(execution);
    } else {
      const execution = pending.get(key)?.shift();
      if (execution) {
        execution.result = payload;
        execution.resultAt = Number.isFinite(event.ts) ? event.ts : null;
      } else {
        executions.push({
          name: toolName(payload.name),
          startedAt: null,
          arguments: null,
          resultAt: Number.isFinite(event.ts) ? event.ts : null,
          result: payload,
        });
      }
    }
  }
  return executions;
}

function executionState(result, live) {
  if (!result) return live ? "pending" : "missing";
  if (result.status >= 400 || result.error) return "error";
  return result.status >= 200 && result.status < 300 ? "success" : "recorded";
}

function durationMs(execution) {
  const measured = execution.result?.ms;
  if (Number.isFinite(measured) && measured >= 0) return measured;
  if (execution.startedAt == null || execution.resultAt == null) return null;
  const elapsed = (execution.resultAt - execution.startedAt) * 1000;
  return elapsed >= 0 ? Math.round(elapsed) : null;
}

function appointmentDate(slot) {
  if (typeof slot !== "string" || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(slot)) return null;
  const date = new Date(slot);
  return Number.isFinite(date.getTime()) ? appointmentFormat.format(date) : null;
}

function actionDescription(action) {
  if (action.state !== "success") return STATE_DESCRIPTIONS[action.state];
  const response = action.result?.response ?? action.result?.result;
  if (action.name === "search_patients" && Array.isArray(response?.matches)) {
    const count = response.matches.length;
    if (count === 0) return "No se han encontrado coincidencias con los datos facilitados.";
    if (count === 1) return "Se ha encontrado una ficha que coincide con los datos facilitados.";
    return `Se han encontrado ${count} posibles coincidencias. La búsqueda no confirma por sí sola la identidad.`;
  }
  if (action.name === "search_availability") {
    const count = Array.isArray(response?.slots) ? response.slots.length : response?.slots;
    if (count === 0) return "No se han encontrado citas disponibles para la búsqueda realizada.";
    if (Number.isInteger(count) && count > 0) return `Se han encontrado ${count} ${count === 1 ? "horario disponible" : "horarios disponibles"}.`;
  }
  if (action.name === "get_patient_appointments" && Array.isArray(response?.appointments)) {
    const count = response.appointments.length;
    return count === 0
      ? "No se han encontrado citas registradas para la consulta realizada."
      : `Se han encontrado ${count} ${count === 1 ? "cita registrada" : "citas registradas"}.`;
  }
  if (action.name === "book_appointment" || action.name === "reschedule_appointment") {
    const date = appointmentDate(action.arguments?.slot);
    if (date) return `Fecha de la cita: ${date} (hora peninsular).`;
  }
  if ((action.name === "submit_no_action" || action.name === "escalate_to_human") && action.arguments?.reason != null) {
    return getActionReason(action.arguments.reason);
  }
  return actionCopy(action.name).description;
}

export function buildAgentActions(events, { live = false } = {}) {
  return pairExecutions(events).map((execution, index) => {
    const action = {
      ...execution,
      order: index + 1,
      state: executionState(execution.result, live),
      durationMs: durationMs(execution),
    };
    const copy = actionCopy(action.name);
    return {
      ...action,
      title: action.state === "success" ? copy.success : copy.label,
      description: actionDescription(action),
    };
  });
}
