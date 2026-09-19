import { useId, useMemo } from "react";

import { buildAgentActions } from "./agentGraph.js";

const STATES = {
  success: { label: "Completed", badge: "bg-emerald-50 text-emerald-700", number: "border-emerald-200 text-emerald-700" },
  error: { label: "Issue", badge: "bg-rose-50 text-rose-700", number: "border-rose-200 text-rose-700" },
  pending: { label: "In progress", badge: "bg-amber-50 text-amber-700", number: "border-amber-200 text-amber-700" },
  missing: { label: "Unconfirmed", badge: "bg-amber-50 text-amber-700", number: "border-amber-200 text-amber-700" },
  recorded: { label: "Unconfirmed", badge: "bg-slate-100 text-slate-600", number: "border-slate-200 text-slate-600" },
};

function actionTime(action, startedAt) {
  const timestamp = action.startedAt ?? action.resultAt;
  if (!Number.isFinite(timestamp) || !Number.isFinite(startedAt)) return "Time not recorded";
  const seconds = Math.max(0, Math.floor(timestamp - startedAt));
  const time = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  return action.startedAt == null ? `Result recorded at ${time}` : `${time} from start`;
}

function ActionItem({ action, startedAt }) {
  const style = STATES[action.state];
  const label = action.state === "success" && action.name === "submit_no_action" ? "Closure recorded" : style.label;
  return (
    <li className="flex gap-3 border-b border-slate-100 py-4 last:border-0">
      <span aria-hidden="true" className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-white text-xs font-semibold ${style.number}`}>
        {action.order}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs tabular-nums text-slate-400">{actionTime(action, startedAt)}</span>
          <span className={`rounded-md px-2 py-1 text-[11px] font-medium ${style.badge}`}>{label}</span>
        </div>
        <h4 className="break-words text-sm font-semibold leading-relaxed text-slate-900">{action.title}</h4>
        <p className="mt-1 break-words text-[13px] leading-relaxed text-slate-500">{action.description}</p>
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
          <h3 id={headingId} className="text-sm font-semibold text-slate-900">Assistant actions</h3>
          <p className="mt-1 text-xs text-slate-400">Recorded activity during the call</p>
        </div>
        <span className="shrink-0 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium tabular-nums text-slate-600">
          {actions.length} {actions.length === 1 ? "action" : "actions"}
        </span>
      </div>
      {actions.length ? (
        <ol
          aria-label="Recorded actions in chronological order"
          tabIndex={0}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-emerald-300"
        >
          {actions.map((action) => <ActionItem key={action.order} action={action} startedAt={startedAt} />)}
        </ol>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 text-center">
          <p className="text-sm font-medium text-slate-700">No recorded actions</p>
          <p className="mt-2 max-w-xs text-sm leading-relaxed text-slate-500">
            {live
              ? "Actions will appear here as the call progresses."
              : "No assistant actions were recorded for this call."}
          </p>
        </div>
      )}
    </section>
  );
}
