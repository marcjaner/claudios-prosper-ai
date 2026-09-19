const GRAPH_KINDS = new Set([
  "request_finished",
  "stage_entered",
  "fact_recorded",
  "tool_rejected",
  "transition_rejected",
  "turn_finished",
]);

const ENDING_LABELS = {
  waiting: "esperando al llamante",
  budget: "límite de pasos",
  failed: "error",
};

// Replaying in order is what makes the panel honest: a fact cleared on entry
// must disappear, not linger because it was recorded earlier.
function replay(events) {
  const facts = new Map();
  let stage = null;
  for (const event of events) {
    if (event.kind === "fact_recorded") {
      facts.set(event.payload.key, { value: event.payload.value, stage: event.payload.stage });
    } else if (event.kind === "stage_entered") {
      stage = event.payload.stage;
      for (const key of event.payload.cleared ?? []) facts.delete(key);
    }
  }
  return { facts, stage };
}

function Entry({ event, startedAt }) {
  const at = startedAt ? `${(event.ts - startedAt).toFixed(1)}s` : "";
  const { kind, payload } = event;

  if (kind === "request_finished") {
    return (
      <Row at={at} tone="text-sky-400">
        petición {payload.request} completada con {payload.action}
        {payload.next === "finished" && <span className="text-slate-500"> · la llamada termina</span>}
      </Row>
    );
  }
  if (kind === "stage_entered") {
    return (
      <Row at={at} tone="text-emerald-400">
        {payload.from ? `${payload.from} → ${payload.stage}` : `empieza en ${payload.stage}`}
        {payload.cleared?.length > 0 && (
          <span className="text-slate-600"> · olvida {payload.cleared.join(", ")}</span>
        )}
      </Row>
    );
  }
  if (kind === "fact_recorded") {
    return (
      <Row at={at} tone="text-slate-300">
        <span className="font-mono">{payload.key}</span>
        <span className="text-slate-600"> = </span>
        <span className="font-mono text-slate-400">{payload.value}</span>
      </Row>
    );
  }
  if (kind === "tool_rejected" || kind === "transition_rejected") {
    return (
      <Row at={at} tone="text-amber-400">
        <span className="font-mono">{payload.requested}</span>
        <span className="text-slate-500"> rechazado · {payload.reason}</span>
      </Row>
    );
  }
  return (
    <Row at={at} tone="text-slate-600">
      turno {payload.turn} termina · {ENDING_LABELS[payload.ending] ?? payload.ending}
    </Row>
  );
}

function Row({ at, tone, children }) {
  return (
    <li className="flex gap-3 py-1 text-xs leading-relaxed">
      <span className="w-12 shrink-0 text-right font-mono text-slate-700">{at}</span>
      <span className={tone}>{children}</span>
    </li>
  );
}

export default function StageTrace({ events, startedAt }) {
  const graphEvents = events.filter((event) => GRAPH_KINDS.has(event.kind));
  if (graphEvents.length === 0) return null;

  const { facts, stage } = replay(graphEvents);

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-baseline gap-2">
        <h3 className="text-xs uppercase tracking-wide text-slate-500">Recorrido del agente</h3>
        {stage && (
          <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">
            {stage}
          </span>
        )}
      </div>

      {facts.size > 0 && (
        <dl className="mb-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-b border-slate-800/70 pb-3">
          {[...facts].map(([key, { value, stage: source }]) => (
            <div key={key} className="contents">
              <dt className="font-mono text-xs text-slate-500">{key}</dt>
              <dd className="font-mono text-xs text-slate-300">
                {value}
                {source && <span className="ml-2 text-slate-700">desde {source}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <ul>
        {graphEvents.map((event, index) => (
          <Entry key={`${event.ts}-${index}`} event={event} startedAt={startedAt} />
        ))}
      </ul>
    </div>
  );
}
