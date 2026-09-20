import { useEffect, useState } from "react";

import AgentActions from "./AgentActionGraph.jsx";
import { StaffBadge } from "./History.jsx";
import { PatientSatisfactionDetail } from "./PatientSatisfaction.jsx";
import StageTrace from "./StageTrace.jsx";
import Transcript from "./Transcript.jsx";
import { getActionReason } from "./agentGraph.js";
import { alertReason, needsAttention, parseScore } from "./alertReason.js";
import { outcomeStyle } from "./outcomes.js";
import { useOperatorLine } from "./useOperatorLine.js";

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

const PILL = "rounded-full px-3 py-1.5 text-xs font-semibold shadow-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2";

const LINE_LABELS = {
  connecting: "Connecting your microphone…",
  handoff: "The agent is handing the call over…",
  live: "You are on the line",
};

// Transferring takes the agent off the call and puts this browser's microphone
// on it, so it takes a second click: the first only reveals the confirmation.
function TransferButton({ callId, heldElsewhere }) {
  const [confirming, setConfirming] = useState(false);
  const { phase, start, hangUp } = useOperatorLine(callId);

  if (phase === "ended") return <span className={`${PILL} bg-violet-100 text-violet-800 shadow-none`}>Transferred to staff</span>;
  if (phase in LINE_LABELS) {
    return (
      <span className="flex items-center gap-2">
        <span className={`${PILL} flex items-center gap-2 bg-amber-100 text-amber-800 shadow-none`}>
          <span className={`inline-block h-2 w-2 rounded-full bg-amber-500 ${phase === "live" ? "animate-pulse" : ""}`} />
          {LINE_LABELS[phase]}
        </span>
        <button type="button" onClick={hangUp} className={`${PILL} bg-rose-600 text-white hover:bg-rose-700 focus-visible:outline-rose-600`}>
          End call
        </button>
      </span>
    );
  }
  if (confirming) {
    return (
      <span className="flex items-center gap-2">
        <button type="button" onClick={() => { setConfirming(false); start(); }} className={`${PILL} bg-violet-600 text-white hover:bg-violet-700 focus-visible:outline-violet-600`}>
          Confirm transfer
        </button>
        <button type="button" onClick={() => setConfirming(false)} className={`${PILL} border border-slate-200 bg-white text-slate-600 hover:border-slate-300 focus-visible:outline-slate-500`}>
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => setConfirming(true)}
      disabled={heldElsewhere}
      title={heldElsewhere ? "A colleague is already on this call" : "Take the call yourself: the agent hands over and your microphone goes live"}
      className={`${PILL} border border-violet-200 bg-white text-violet-700 hover:border-violet-300 hover:bg-violet-50 focus-visible:outline-violet-600 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400 disabled:hover:bg-white`}
    >
      {phase === "error" ? "Transfer failed · retry" : "Transfer call"}
    </button>
  );
}

function AttentionWarning({ callId, reason }) {
  const [status, setStatus] = useState("idle");

  const terminateCall = async () => {
    setStatus("stopping");
    try {
      const response = await fetch(`/api/calls/${encodeURIComponent(callId)}/stop`, { method: "POST" });
      if (!response.ok) throw new Error("stop failed");
    } catch {
      setStatus("error");
    }
  };

  return (
    <aside role="alert" className="flex shrink-0 flex-col gap-4 rounded-2xl border border-amber-200 bg-amber-50/70 p-4 shadow-[0_1px_2px_rgba(120,53,15,0.04)] sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-amber-100 text-lg font-bold text-amber-700">
          !
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-amber-950">This call needs attention</p>
          <p className="mt-0.5 text-sm leading-relaxed text-amber-900/80">
            {status === "error" ? "The call could not be terminated. Please try again." : reason}
          </p>
        </div>
      </div>
      <button
        type="button"
        onClick={terminateCall}
        disabled={status === "stopping"}
        className="shrink-0 self-start rounded-full bg-rose-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-rose-700 disabled:cursor-wait disabled:bg-rose-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 sm:self-auto"
      >
        {status === "stopping" ? "Terminating…" : status === "error" ? "Retry termination" : "Terminate call"}
      </button>
    </aside>
  );
}

// The agent reads staff instructions on the caller's next turn, so sending one
// never interrupts anybody; the echo is the "Clinic staff" bubble in the transcript.
function StaffInstruction({ callId }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState("idle");

  const send = async (event) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || status === "sending") return;
    setStatus("sending");
    try {
      const response = await fetch(`/api/calls/${callId}/instructions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: trimmed }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setText("");
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  };

  return (
    <form onSubmit={send} className="border-t border-slate-100 px-4 py-3">
      <label htmlFor={`instruction-${callId}`} className="text-xs font-medium text-violet-700">
        Instruct the agent
      </label>
      <div className="mt-1.5 flex gap-2">
        <input
          id={`instruction-${callId}`}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="e.g. Only offer afternoon slots"
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-violet-300 focus:outline-none focus:ring-2 focus:ring-violet-100"
        />
        <button
          type="submit"
          disabled={!text.trim() || status === "sending"}
          className={`${PILL} bg-violet-600 text-white hover:bg-violet-700 focus-visible:outline-violet-600 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400`}
        >
          {status === "sending" ? "Sending…" : "Send to agent"}
        </button>
      </div>
      <p className={`mt-1.5 text-[11px] ${status === "error" ? "text-rose-600" : "text-slate-400"}`}>
        {status === "error" ? "Could not send: the call may have ended." : "Applied on the patient's next turn."}
      </p>
    </form>
  );
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
  const score = parseScore(call.score_json);
  const attention = live && call.state !== "operator" && needsAttention(call, score, live);
  const attentionReason = attention ? alertReason(call, score) ?? "Clinic staff should review this call" : null;

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
          {live && <TransferButton key={callId} callId={callId} heldElsewhere={call.state === "operator"} />}
          <span className={`rounded-full px-3 py-1.5 text-xs font-semibold ${live ? "bg-emerald-100 text-emerald-800" : "bg-slate-200/70 text-slate-600"}`}>
            {live ? "In progress" : "Completed"}
          </span>
          {call.handled_by === "operator" && <StaffBadge className="px-3 py-1.5" />}
          {style && (
            <span className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700">
              <span style={{ backgroundColor: style.color }} className="h-1.5 w-1.5 rounded-full" />
              {style.label}
            </span>
          )}
          {call.reason && <span className="max-w-sm text-xs leading-relaxed text-slate-500">{getActionReason(call.reason)}</span>}
          {!live && call.guardrail_breached && <span className="rounded-full bg-amber-100 px-3 py-1.5 text-xs font-semibold text-amber-800">Safety rule breached</span>}
        </div>
      </header>

      {attention && <AttentionWarning key={callId} callId={callId} reason={attentionReason} />}

      <aside aria-label="Patient information" className="clinic-panel min-w-0 shrink-0 p-5">
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.8fr)]">
          <div className="min-w-0">
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
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
              <Field label="Patient ID">{call.patient_id}</Field>
              <Field label="Caller">{call.from_number ?? "Hidden"}</Field>
              <Field label="Started">{timeFormat.format(new Date(call.started_at * 1000))}</Field>
              <Field label="Duration">{formatDuration(call)}</Field>
              <Field label="First response">{call.ttfa_seconds != null ? `${call.ttfa_seconds.toFixed(2)} s` : null}</Field>
              <Field label="Cost">{call.cost_eur != null ? `${call.cost_eur.toFixed(4)} €` : null}</Field>
            </dl>
            {call.pricing && (
              <div className="mt-4 rounded-xl bg-slate-50 px-3 py-3 text-xs text-slate-600">
                <p className="font-semibold text-slate-800">Estimated provider cost</p>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-5">
                  <span>LLM ${(call.pricing.llm_usd ?? 0).toFixed(6)}</span>
                  <span>STT ${(call.pricing.stt_usd ?? 0).toFixed(6)}</span>
                  <span>TTS ${(call.pricing.tts_usd ?? 0).toFixed(6)}</span>
                  <span>JEV ${(call.pricing.jev_usd ?? 0).toFixed(6)}</span>
                  <span className="font-semibold text-slate-800">Total ${(call.pricing.total_usd ?? 0).toFixed(6)}</span>
                </div>
              </div>
            )}
            {call.error && <p className="mt-3 break-words rounded-xl bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-700">{call.error}</p>}
          </div>
          <PatientSatisfactionDetail
            events={events}
            startedAt={call.started_at}
            endedAt={call.ended_at}
          />
        </div>
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
          {live && call.state !== "operator" && <StaffInstruction key={callId} callId={callId} />}
        </section>
      </div>
    </main>
  );
}
