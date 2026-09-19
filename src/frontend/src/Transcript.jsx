import { useEffect, useRef, useState } from "react";

import { getActionLabel } from "./agentGraph.js";

const SPEAKERS = {
  tts: { who: "Agente", tone: "text-emerald-300" },
  llm: { who: "Razonamiento", tone: "text-slate-400" },
  stt_final: { who: "Paciente", tone: "text-slate-300" },
  stt_partial: { who: "Paciente", tone: "text-slate-400" },
};

/** A request and its response are one thing that happened, so show one chip. */
function pairTools(events) {
  const paired = [];
  const byKey = new Map();
  for (const event of events) {
    const key = event.payload.tool_call_id ?? event.payload.name;
    if (event.kind === "tool_call") {
      const entry = { ...event, result: null };
      byKey.set(key, entry);
      paired.push(entry);
    } else if (event.kind === "tool_result" && byKey.has(key)) {
      const entry = byKey.get(key);
      entry.result = event;
      byKey.delete(key);
    } else {
      paired.push(event);
    }
  }
  return paired;
}

// Lifecycle events (call_started, call_ended) belong to the card, not to the
// conversation, so the transcript never shows them.
const RENDERED = new Set([
  ...Object.keys(SPEAKERS),
  "tool_call",
  "tool_result",
  "submit",
  "error",
]);

function offset(event, startedAt) {
  const seconds = Math.max(0, event.ts - startedAt);
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function ToolChip({ event, live }) {
  const [open, setOpen] = useState(false);
  const { payload } = event;
  const response = event.result?.payload ?? {};
  const status = response.status;
  const failed = status >= 400 || response.error;
  // A call with no result yet is still in flight — that is worth seeing live.
  const pending = event.kind === "tool_call" && !event.result;
  const milliseconds =
    response.ms ?? (event.result ? Math.round((event.result.ts - event.ts) * 1000) : null);
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${failed ? "border-rose-900/60 bg-rose-500/5" : "border-slate-800/80 bg-slate-950/40"}`}>
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-300"
      >
        <span aria-hidden="true" className="text-slate-400">{open ? "▾" : "▸"}</span>
        <span className="min-w-0 break-words text-xs font-medium text-slate-300">
          {payload.name ? getActionLabel(payload.name) : event.kind === "submit" ? "Registro de la gestión" : event.kind === "error" ? "Incidencia durante la llamada" : "Actividad del asistente"}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2 font-mono text-[11px]">
          {pending && <span className="text-amber-300">{live ? "en curso" : "sin resultado"}</span>}
          {status && <span className={failed ? "text-rose-300" : "text-emerald-300/80"}>{status}</span>}
          {milliseconds != null && <span className="text-slate-400">{milliseconds} ms</span>}
        </span>
      </button>
      {open && (
        <pre className="mt-3 overflow-x-auto border-t border-slate-800 pt-3 font-mono text-xs leading-relaxed text-slate-300">
          {payload.name ?? payload.route ?? event.kind}
          {payload.url ? `\n${payload.url}` : ""}
          {"\n"}
          {JSON.stringify(payload.request ?? payload.arguments ?? {}, null, 2)}
          {"\n"}
          {JSON.stringify(response.response ?? response.result ?? response.error ?? {}, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function Transcript({ events: incoming, startedAt, live }) {
  const scrollContainer = useRef(null);
  const [pinned, setPinned] = useState(true);
  const events = incoming.filter((event) => RENDERED.has(event.kind));

  useEffect(() => {
    if (live && pinned) {
      scrollContainer.current?.scrollTo({
        top: scrollContainer.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [events, live, pinned]);

  if (events.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-6 text-center">
        <p className="text-sm font-medium text-slate-300">
          {live ? "Esperando conversación" : "Sin conversación registrada"}
        </p>
        <p className="mt-2 max-w-xs text-sm leading-relaxed text-slate-400">
          {live
            ? "La transcripción aparecerá aquí cuando empiece la conversación."
            : "Esta llamada no tiene una transcripción disponible."}
        </p>
      </div>
    );
  }

  return (
    <div
      ref={scrollContainer}
      onScroll={(event) => {
        const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
        setPinned(scrollHeight - scrollTop - clientHeight < 40);
      }}
      className="h-full min-h-0 space-y-3 overflow-y-auto overscroll-contain pr-1"
    >
      {pairTools(events).map((event, index) => {
        const speaker = SPEAKERS[event.kind];
        return (
          <div key={event.id ?? `${event.ts}-${index}`} className="flex gap-2.5 text-sm">
            <span className="w-9 shrink-0 pt-3 font-mono text-[11px] tabular-nums text-slate-500">
              {offset(event, startedAt)}
            </span>
            <div className="min-w-0 flex-1">
              {speaker ? (
                <div className={`rounded-xl px-3.5 py-3 ${event.kind === "tts" ? "bg-emerald-400/[0.06]" : "bg-slate-800/45"}`}>
                  <p className={`mb-1 text-xs font-medium ${speaker.tone}`}>{speaker.who}</p>
                  <p className={`break-words leading-relaxed ${event.kind === "stt_partial" ? "text-slate-400 italic" : "text-slate-200"}`}>
                    {event.payload.text}
                  </p>
                </div>
              ) : (
                <ToolChip event={event} live={live} />
              )}
            </div>
          </div>
        );
      })}
      {live && !pinned && (
        <button
          type="button"
          onClick={() => setPinned(true)}
          className="sticky bottom-2 mx-auto block rounded-lg border border-emerald-800 bg-emerald-950 px-3 py-2 text-xs font-medium text-emerald-200 focus-visible:outline-2 focus-visible:outline-emerald-300"
        >
          Volver al directo
        </button>
      )}
    </div>
  );
}
