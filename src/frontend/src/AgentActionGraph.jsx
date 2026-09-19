import { useId, useMemo } from "react";

import { buildAgentActions } from "./agentGraph.js";

const STATES = {
  success: { label: "Completada", badge: "bg-emerald-500/10 text-emerald-300", number: "border-emerald-800/60 text-emerald-300" },
  error: { label: "Con incidencia", badge: "bg-rose-500/10 text-rose-300", number: "border-rose-800/60 text-rose-300" },
  pending: { label: "En curso", badge: "bg-amber-500/10 text-amber-300", number: "border-amber-800/60 text-amber-300" },
  missing: { label: "Sin confirmar", badge: "bg-amber-500/10 text-amber-300", number: "border-amber-800/60 text-amber-300" },
  recorded: { label: "Sin confirmar", badge: "bg-slate-800 text-slate-300", number: "border-slate-700 text-slate-300" },
};

function actionTime(action, startedAt) {
  const timestamp = action.startedAt ?? action.resultAt;
  if (!Number.isFinite(timestamp) || !Number.isFinite(startedAt)) return "Momento no registrado";
  const seconds = Math.max(0, Math.floor(timestamp - startedAt));
  const time = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  return action.startedAt == null ? `Resultado registrado a los ${time}` : `${time} desde el inicio`;
}

function ActionItem({ action, startedAt }) {
  const style = STATES[action.state];
  const label = action.state === "success" && action.name === "submit_no_action" ? "Cierre registrado" : style.label;
  return (
    <li className="flex gap-3 border-b border-slate-800/70 py-4 last:border-0">
      <span aria-hidden="true" className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-slate-950/40 font-mono text-xs ${style.number}`}>
        {action.order}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs tabular-nums text-slate-400">{actionTime(action, startedAt)}</span>
          <span className={`rounded-md px-2 py-1 text-[11px] font-medium ${style.badge}`}>{label}</span>
        </div>
        <h4 className="break-words text-sm font-semibold leading-relaxed text-slate-100">{action.title}</h4>
        <p className="mt-1 break-words text-[13px] leading-relaxed text-slate-400">{action.description}</p>
      </div>
    </li>
  );
}

export default function AgentActions({ events, startedAt, live = false }) {
  const actions = useMemo(() => buildAgentActions(events, { live }), [events, live]);
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="call-panel">
      <div className="call-panel-header">
        <div>
          <h3 id={headingId} className="text-sm font-semibold text-slate-100">Acciones del agente</h3>
          <p className="mt-1 text-xs text-slate-400">Gestiones registradas durante la llamada</p>
        </div>
        <span className="shrink-0 rounded-md bg-slate-800/70 px-2 py-1 text-xs tabular-nums text-slate-300">
          {actions.length} {actions.length === 1 ? "gestión" : "gestiones"}
        </span>
      </div>
      {actions.length ? (
        <ol
          aria-label="Acciones registradas en orden cronológico"
          tabIndex={0}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-emerald-300"
        >
          {actions.map((action) => <ActionItem key={action.order} action={action} startedAt={startedAt} />)}
        </ol>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 text-center">
          <p className="text-sm font-medium text-slate-300">No hay acciones registradas</p>
          <p className="mt-2 max-w-xs text-sm leading-relaxed text-slate-400">
            {live
              ? "Las gestiones aparecerán aquí a medida que avance la llamada."
              : "No se han registrado gestiones del asistente en esta llamada."}
          </p>
        </div>
      )}
    </section>
  );
}
