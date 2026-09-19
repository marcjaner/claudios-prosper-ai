import { useEffect, useRef, useState } from "react";

const SPEAKERS = {
  tts: { who: "Agente", tone: "text-emerald-300" },
  llm: { who: "Razonamiento", tone: "text-violet-300" },
  stt_final: { who: "Paciente", tone: "text-sky-300" },
  stt_partial: { who: "Paciente", tone: "text-sky-300" },
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
  "guardrail_breach",
]);

function offset(event, startedAt) {
  const seconds = Math.max(0, event.ts - startedAt);
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function ToolChip({ event }) {
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
    <div
      className={`rounded-lg border bg-slate-950/60 px-3 py-2 ${
        failed ? "border-rose-800/70" : "border-slate-800"
      }`}
    >
      <button
        onClick={() => setOpen((previous) => !previous)}
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="text-slate-600">{open ? "▾" : "▸"}</span>
        <span className="font-mono text-xs text-slate-300">
          {payload.name ?? payload.route ?? event.kind}
        </span>
        {payload.url && <span className="font-mono text-xs text-slate-600">{payload.url}</span>}
        <span className="ml-auto flex items-center gap-2 font-mono text-xs">
          {pending && <span className="animate-pulse text-amber-400">en curso</span>}
          {status && <span className={failed ? "text-rose-400" : "text-slate-500"}>{status}</span>}
          {milliseconds != null && <span className="text-slate-600">{milliseconds} ms</span>}
        </span>
      </button>
      {open && (
        <pre className="mt-2 overflow-x-auto border-t border-slate-800 pt-2 font-mono text-xs leading-relaxed text-slate-400">
          {JSON.stringify(payload.request ?? payload.arguments ?? {}, null, 2)}
          {"\n"}
          {JSON.stringify(response.response ?? response.result ?? response.error ?? {}, null, 2)}
        </pre>
      )}
    </div>
  );
}

function GuardrailBreach({ event }) {
  return (
    <div className="rounded-lg border border-amber-800/70 bg-amber-950/30 px-3 py-2 text-amber-300">
      <span className="mr-2">⚠</span>
      <span className="text-xs uppercase tracking-wide">Safety rule breach</span>
      <p className="mt-1 text-xs text-amber-200/80">{event.payload.reason}</p>
    </div>
  );
}

export default function Transcript({ events: incoming, startedAt, live }) {
  const bottom = useRef(null);
  const [pinned, setPinned] = useState(true);
  const events = incoming.filter((event) => RENDERED.has(event.kind));

  useEffect(() => {
    if (live && pinned) bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [events, live, pinned]);

  if (events.length === 0) {
    return (
      <p className="py-16 text-center text-sm text-slate-600">
        {live
          ? "Esperando a que alguien hable…"
          : "Esta llamada no dejó transcripción. Llegará cuando el STT esté en el pipeline."}
      </p>
    );
  }

  return (
    <div
      onScroll={(event) => {
        const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
        setPinned(scrollHeight - scrollTop - clientHeight < 40);
      }}
      className="max-h-[60vh] space-y-2 overflow-y-auto pr-1"
    >
      {pairTools(events).map((event, index) => {
        const speaker = SPEAKERS[event.kind];
        return (
          <div key={event.id ?? `${event.ts}-${index}`} className="flex gap-3 text-sm">
            <span className="w-10 shrink-0 pt-0.5 font-mono text-xs text-slate-600">
              {offset(event, startedAt)}
            </span>
            <div className="min-w-0 flex-1">
              {speaker ? (
                <p>
                  <span className={`mr-2 text-xs uppercase tracking-wide ${speaker.tone}`}>
                    {speaker.who}
                  </span>
                  <span
                    className={
                      event.kind === "stt_partial" ? "text-slate-500 italic" : "text-slate-200"
                    }
                  >
                    {event.payload.text}
                  </span>
                </p>
              ) : event.kind === "guardrail_breach" ? (
                <GuardrailBreach event={event} />
              ) : (
                <ToolChip event={event} />
              )}
            </div>
          </div>
        );
      })}
      <div ref={bottom} />
      {live && !pinned && (
        <button
          onClick={() => setPinned(true)}
          className="sticky bottom-2 mx-auto block rounded-full bg-emerald-500/20 px-3 py-1 text-xs text-emerald-300"
        >
          volver al directo
        </button>
      )}
    </div>
  );
}
