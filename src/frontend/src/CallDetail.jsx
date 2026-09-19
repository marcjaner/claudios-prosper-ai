import { useEffect, useState } from "react";

import AgentActions from "./AgentActionGraph.jsx";
import StageTrace from "./StageTrace.jsx";
import Transcript from "./Transcript.jsx";
import { getActionReason } from "./agentGraph.js";
import { outcomeStyle } from "./outcomes.js";

const timeFormat = new Intl.DateTimeFormat("es-ES", {
  dateStyle: "short",
  timeStyle: "medium",
  timeZone: "Europe/Madrid",
});

function Field({ label, children }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="mt-1 break-words text-[13px] font-medium tabular-nums text-slate-200">
        {children ?? <span className="font-normal text-slate-500">No disponible</span>}
      </dd>
    </div>
  );
}

function formatDuration(call) {
  if (!call.ended_at) return "En curso";
  const seconds = Math.max(0, Math.round(call.ended_at - call.started_at));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function Submission({ event }) {
  const failed = event.payload.status >= 400;
  return (
    <details className="rounded-xl border border-slate-800/80 bg-slate-900/40 px-4 py-3">
      <summary className="cursor-pointer rounded text-sm font-medium text-slate-300 focus-visible:outline-2 focus-visible:outline-emerald-400">
        Lo que se envió
        <span className={`ml-3 font-mono text-xs ${failed ? "text-rose-300" : "text-emerald-300"}`}>{event.payload.status}</span>
      </summary>
      <p className="mt-3 break-all font-mono text-xs text-slate-400">{event.payload.route}</p>
      <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-950/70 p-3 font-mono text-xs leading-relaxed text-slate-300">
        {JSON.stringify(event.payload.request ?? {}, null, 2)}
      </pre>
    </details>
  );
}

export default function CallDetail({ callId, liveCall, liveEvents }) {
  const [stored, setStored] = useState(null);
  const [loading, setLoading] = useState(true);

  // The store holds everything committed so far; the socket carries whatever
  // has happened since. A call opened mid-flight needs both.
  useEffect(() => {
    let stale = false;
    setLoading(true);
    fetch(`/api/calls/${callId}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => !stale && setStored(body))
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
  }, [callId]);

  const call = { ...(stored?.call ?? {}), ...(liveCall ?? {}) };
  const live = call.started_at && !call.ended_at;

  const seen = new Set((stored?.events ?? []).map((event) => `${event.ts}-${event.kind}`));
  const events = [
    ...(stored?.events ?? []),
    ...liveEvents.filter((event) => !seen.has(`${event.ts}-${event.kind}`)),
  ].sort((a, b) => a.ts - b.ts);

  const submission = events.find((event) => event.kind === "submit");
  const hasStageTrace = events.some(({ kind }) =>
    ["stage_entered", "fact_recorded", "tool_rejected", "transition_rejected", "turn_finished"].includes(kind),
  );
  const style = call.outcome ? outcomeStyle(call.outcome) : null;

  if (loading && !liveCall) {
    return <p className="p-6 text-slate-400">Cargando llamada…</p>;
  }
  if (!call.started_at) {
    return <p className="p-6 text-slate-400">No hay ninguna llamada con ese identificador.</p>;
  }

  return (
    <main className="mx-auto max-w-[1680px] space-y-4 px-4 py-5 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <a href={live ? "#/wall" : "#/historico"} className="rounded text-xs text-slate-400 transition-colors hover:text-slate-200 focus-visible:outline-2 focus-visible:outline-emerald-400">
            ← {live ? "Volver al Wall" : "Volver al Histórico"}
          </a>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 className="text-xl font-semibold tracking-tight text-slate-100">Detalle de llamada</h2>
            <span className="break-all font-mono text-[11px] text-slate-500">{callId}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-lg px-2.5 py-1.5 text-xs font-medium ${live ? "bg-emerald-500/10 text-emerald-300" : "bg-slate-800/70 text-slate-400"}`}>
            {live ? "En curso" : "Finalizada"}
          </span>
          {style && (
            <span className="flex items-center gap-2 rounded-lg border border-slate-800 px-2.5 py-1.5 text-xs text-slate-200">
              <span style={{ backgroundColor: style.color }} className="h-1.5 w-1.5 rounded-full" />
              {style.label}
            </span>
          )}
          {call.reason && <span className="max-w-sm text-xs leading-relaxed text-slate-400">{getActionReason(call.reason)}</span>}
        </div>
      </header>

      <aside aria-label="Información del paciente" className="min-w-0 rounded-2xl border border-slate-800/80 bg-slate-900/50 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs text-slate-400">Paciente</p>
            <h3 className="mt-0.5 break-words text-lg font-semibold tracking-tight text-slate-100">
              {call.patient_name ?? "Sin identificar"}
            </h3>
          </div>
          <span className="shrink-0 rounded-md bg-slate-800/80 px-2 py-1 text-xs text-slate-300">
            {call.insurer ?? "Seguro sin identificar"}
          </span>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 xl:grid-cols-6">
          <Field label="Ficha">{call.patient_id}</Field>
          <Field label="Llamante">{call.from_number ?? "Oculto"}</Field>
          <Field label="Inicio">{timeFormat.format(new Date(call.started_at * 1000))}</Field>
          <Field label="Duración">{formatDuration(call)}</Field>
          <Field label="Primera voz">{call.ttfa_seconds != null ? `${call.ttfa_seconds.toFixed(2)} s` : null}</Field>
          <Field label="Coste">{call.cost_eur != null ? `${call.cost_eur.toFixed(4)} €` : null}</Field>
        </dl>
        {call.error && <p className="mt-3 break-words rounded-lg bg-rose-500/5 px-3 py-2 text-xs leading-relaxed text-rose-300">{call.error}</p>}
      </aside>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <AgentActions key={callId} events={events} startedAt={call.started_at} live={live} />
        <section aria-label="Transcripción y traza" className="call-panel">
          <div className="call-panel-header">
            <div>
              <h3 className="text-sm font-semibold text-slate-100">Transcripción y traza</h3>
              <p className="mt-1 text-xs text-slate-400">Conversación y gestiones realizadas</p>
            </div>
            <span className="rounded-md bg-slate-800/70 px-2 py-1 text-[11px] text-slate-400">{live ? "En directo" : "Registro"}</span>
          </div>
          <div className="min-h-0 flex-1 p-4">
            <Transcript events={events} startedAt={call.started_at} live={live} />
          </div>
        </section>
      </div>

      {hasStageTrace && (
        <details className="rounded-xl border border-slate-800/80 bg-slate-900/40 px-4 py-3">
          <summary className="cursor-pointer rounded text-sm font-medium text-slate-300 focus-visible:outline-2 focus-visible:outline-emerald-400">
            Detalle técnico del recorrido
          </summary>
          <div className="mt-3">
            <StageTrace events={events} startedAt={call.started_at} />
          </div>
        </details>
      )}
      {submission && <Submission event={submission} />}
    </main>
  );
}
