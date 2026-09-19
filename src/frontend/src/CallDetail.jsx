import { useEffect, useState } from "react";

import StageTrace from "./StageTrace.jsx";
import Transcript from "./Transcript.jsx";
import { outcomeStyle } from "./outcomes.js";

const timeFormat = new Intl.DateTimeFormat("es-ES", {
  dateStyle: "short",
  timeStyle: "medium",
  timeZone: "Europe/Madrid",
});

function Field({ label, children }) {
  return (
    <div className="border-t border-slate-800/70 py-2 first:border-0">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-200">{children ?? <span className="text-slate-600">—</span>}</dd>
    </div>
  );
}

function Submission({ event }) {
  const failed = event.payload.status >= 400;
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
      <p className="flex items-center gap-2 font-mono text-xs">
        <span className="text-slate-400">{event.payload.route}</span>
        <span className={`ml-auto ${failed ? "text-rose-400" : "text-emerald-400"}`}>
          {event.payload.status}
        </span>
      </p>
      <pre className="mt-2 overflow-x-auto font-mono text-xs leading-relaxed text-slate-400">
        {JSON.stringify(event.payload.request ?? {}, null, 2)}
      </pre>
    </div>
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
  const style = call.outcome ? outcomeStyle(call.outcome) : null;

  if (loading && !liveCall) {
    return <p className="p-6 text-slate-600">Cargando…</p>;
  }
  if (!call.started_at) {
    return <p className="p-6 text-slate-600">No hay ninguna llamada con ese identificador.</p>;
  }

  return (
    <main className="p-6">
      <a href="#/wall" className="text-sm text-slate-500 hover:text-slate-300">
        ← volver
      </a>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <h2 className="font-mono text-sm text-slate-400">{callId}</h2>
        {live && (
          <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1 text-xs text-emerald-400">
            <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
            en curso
          </span>
        )}
        {style && (
          <span className="flex items-center gap-1.5 text-sm">
            <span style={{ backgroundColor: style.color }} className="h-2 w-2 rounded-full" />
            <span className="text-slate-200">{style.label}</span>
            {call.reason && <span className="font-mono text-xs text-slate-500">{call.reason}</span>}
          </span>
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[18rem_1fr]">
        <aside className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <h3 className="mb-2 text-xs uppercase tracking-wide text-slate-500">Paciente</h3>
          <dl>
            <Field label="Nombre">{call.patient_name}</Field>
            <Field label="Ficha">{call.patient_id}</Field>
            <Field label="Seguro">
              {call.insurer && (
                <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs">
                  {call.insurer}
                </span>
              )}
            </Field>
            <Field label="Llamante">{call.from_number ?? "oculto"}</Field>
            <Field label="Inicio">
              {call.started_at && timeFormat.format(new Date(call.started_at * 1000))}
            </Field>
            <Field label="Primera voz">
              {call.ttfa_seconds != null && `${call.ttfa_seconds.toFixed(2)} s`}
            </Field>
            <Field label="Coste">
              {call.cost_eur != null && `${call.cost_eur.toFixed(4)} €`}
            </Field>
          </dl>
          {call.error && (
            <p className="mt-3 rounded border border-rose-900/60 bg-rose-950/40 p-2 text-xs text-rose-300">
              {call.error}
            </p>
          )}
        </aside>

        <section className="space-y-4">
          <StageTrace events={events} startedAt={call.started_at} />
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <h3 className="mb-3 text-xs uppercase tracking-wide text-slate-500">
              Transcripción y traza
            </h3>
            <Transcript events={events} startedAt={call.started_at} live={live} />
          </div>
          {submission && (
            <div>
              <h3 className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Lo que se envió
              </h3>
              <Submission event={submission} />
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
