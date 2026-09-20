import { useEffect, useState } from "react";

import AgentActions from "./AgentActionGraph.jsx";
import StageTrace from "./StageTrace.jsx";
import Transcript from "./Transcript.jsx";
import { getActionReason } from "./agentGraph.js";
import { outcomeStyle } from "./outcomes.js";

const timeFormat = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "short",
  timeStyle: "medium",
  timeZone: "Europe/Madrid",
});

function Field({ label, children }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="mt-1 break-words text-[13px] font-semibold tabular-nums text-slate-800">
        {children ?? <span className="font-normal text-slate-400">Not available</span>}
      </dd>
    </div>
  );
}

function formatDuration(call) {
  if (!call.ended_at) return "In progress";
  const seconds = Math.max(0, Math.round(call.ended_at - call.started_at));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export default function CallDetail({ callId, liveCall, liveEvents, returnTo }) {
  const [stored, setStored] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [callId]);

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

  const hasStageTrace = events.some(({ kind }) =>
    ["stage_entered", "fact_recorded", "tool_rejected", "transition_rejected", "turn_finished"].includes(kind),
  );
  const style = call.outcome ? outcomeStyle(call.outcome) : null;

  if (loading && !liveCall) {
    return <p className="clinic-page text-slate-500">Loading call…</p>;
  }
  if (!call.started_at) {
    return <p className="clinic-page text-slate-500">No call exists with that identifier.</p>;
  }

  return (
    <main className="clinic-page space-y-5 xl:flex xl:min-h-0 xl:w-full xl:flex-1 xl:flex-col xl:gap-5 xl:space-y-0 xl:overflow-hidden">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <a href={returnTo} className="rounded text-sm font-medium text-emerald-700 transition-colors hover:text-emerald-900 focus-visible:outline-2 focus-visible:outline-emerald-500">
            ← Back
          </a>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-3xl font-semibold tracking-[-0.04em] text-slate-950">Conversation detail</h1>
            <span className="break-all text-[11px] text-slate-400">{callId}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-3 py-1.5 text-xs font-semibold ${live ? "bg-emerald-100 text-emerald-800" : "bg-slate-200/70 text-slate-600"}`}>
            {live ? "In progress" : "Completed"}
          </span>
          {style && (
            <span className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700">
              <span style={{ backgroundColor: style.color }} className="h-1.5 w-1.5 rounded-full" />
              {style.label}
            </span>
          )}
          {call.reason && <span className="max-w-sm text-xs leading-relaxed text-slate-500">{getActionReason(call.reason)}</span>}
          {Number(call.guardrail_breached) === 1 && <span className="rounded-full bg-amber-100 px-3 py-1.5 text-xs font-semibold text-amber-800">Safety rule breached</span>}
        </div>
      </header>

      <aside aria-label="Patient information" className="clinic-panel min-w-0 shrink-0 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs text-slate-400">Patient</p>
            <h2 className="mt-0.5 break-words text-xl font-semibold tracking-tight text-slate-950">
              {call.patient_name ?? "Unidentified caller"}
            </h2>
          </div>
          <span className="shrink-0 rounded-full bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-600">
            {call.insurer ?? "Insurer unknown"}
          </span>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 xl:grid-cols-6">
          <Field label="Patient ID">{call.patient_id}</Field>
          <Field label="Caller">{call.from_number ?? "Hidden"}</Field>
          <Field label="Started">{timeFormat.format(new Date(call.started_at * 1000))}</Field>
          <Field label="Duration">{formatDuration(call)}</Field>
          <Field label="First response">{call.ttfa_seconds != null ? `${call.ttfa_seconds.toFixed(2)} s` : null}</Field>
          <Field label="Cost">{call.cost_eur != null ? `${call.cost_eur.toFixed(4)} €` : null}</Field>
        </dl>
        {call.error && <p className="mt-3 break-words rounded-xl bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-700">{call.error}</p>}
      </aside>

      <div className="call-detail-panels grid min-h-0 gap-4 xl:flex-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] xl:overflow-hidden">
        <AgentActions key={callId} events={events} startedAt={call.started_at} live={live}>
          {hasStageTrace && (
            <details className="border-t border-slate-100 px-4 py-4">
              <summary className="cursor-pointer rounded text-sm font-semibold text-slate-700 focus-visible:outline-2 focus-visible:outline-emerald-500">
                Technical journey details
              </summary>
              <div className="mt-3">
                <StageTrace events={events} startedAt={call.started_at} />
              </div>
            </details>
          )}
        </AgentActions>
        <section aria-label="Conversation transcript" className="call-panel">
          <div className="call-panel-header">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">Conversation</h3>
              <p className="mt-1 text-xs text-slate-400">Transcript and assistant activity</p>
            </div>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500">{live ? "Live" : "Record"}</span>
          </div>
          <div className="min-h-0 flex-1 p-4">
            <Transcript events={events} startedAt={call.started_at} live={live} />
          </div>
        </section>
      </div>
    </main>
  );
}
