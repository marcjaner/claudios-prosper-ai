import { useEffect, useState } from "react";

import WallOverview from "./WallOverview.jsx";

// Every call is cut off at three minutes, so duration is a countdown.
const CALL_LIMIT_SECONDS = 180;
const WARN_SECONDS = 150;
const CRITICAL_SECONDS = 170;
const STATE_LABELS = {
  connected: "connecting",
  speaking: "speaking",
  listening: "listening",
  thinking: "thinking",
  ended: "completed",
  lost: "disconnected",
};

function useNow(intervalMs = 250) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function formatDuration(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function ringColor(seconds) {
  if (seconds >= CRITICAL_SECONDS) return "text-rose-500";
  if (seconds >= WARN_SECONDS) return "text-amber-500";
  return "text-emerald-500";
}

function DurationRing({ seconds, live }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.min(1, Math.max(0, seconds / CALL_LIMIT_SECONDS));
  return (
    <div className="relative h-16 w-16 shrink-0">
      <svg className="h-full w-full -rotate-90" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r={radius} fill="none" strokeWidth="5" className="stroke-slate-100" />
        <circle
          cx="32"
          cy="32"
          r={radius}
          fill="none"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - progress)}
          className={`${live ? ringColor(seconds) : "text-slate-300"} stroke-current transition-[stroke-dashoffset] duration-300`}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-sm font-semibold tabular-nums text-slate-800">
        {formatDuration(seconds)}
      </div>
    </div>
  );
}

function parseScoreJson(raw) {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function CallCard({ call, now, dense }) {
  const live = !call.ended_at;
  const seconds = (live ? now : call.ended_at) - call.started_at;
  const state = STATE_LABELS[call.state] ?? call.state ?? "—";
  const score = parseScoreJson(call.score_json);
  const turnCount = score?.turn_count ?? 0;
  const hasScore = typeof call.score_overall === "number";
  const alert = live && turnCount >= 2 && hasScore && call.score_overall < 40;

  return (
    <a
      href={`#/call/${call.call_id}`}
      className={`block rounded-[20px] border p-5 shadow-[0_1px_2px_rgba(15,23,42,0.03)] transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-md ${
        alert
          ? "border-rose-300 bg-rose-50 ring-1 ring-rose-200"
          : `bg-white ${live ? "border-slate-200" : "border-slate-200 opacity-70"}`
      }`}
    >
      <div className="flex items-start gap-4">
        <DurationRing seconds={seconds} live={live} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-slate-900">
            {call.patient_id ?? "unidentified caller"}
          </p>
          <p className="truncate text-xs text-slate-400">
            {call.from_number ?? "hidden number"}
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                live ? "animate-pulse bg-emerald-400" : "bg-slate-600"
              }`}
            />
            <span className="text-slate-600">{state}</span>
            {alert ? (
              <span className="inline-flex animate-pulse items-center gap-1 rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700">
                Review · score {Math.round(call.score_overall)}
              </span>
            ) : (
              live &&
              hasScore && (
                <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold tabular-nums text-slate-600">
                  Score {Math.round(call.score_overall)}
                </span>
              )
            )}
          </p>
        </div>
      </div>
      {!dense && (
        <dl className="mt-4 flex gap-4 border-t border-slate-100 pt-4 text-xs text-slate-400">
          <div>
            <dt>First response</dt>
            <dd className="mt-1 font-semibold tabular-nums text-slate-700">
              {call.ttfa_seconds == null ? "—" : `${call.ttfa_seconds.toFixed(2)}s`}
            </dd>
          </div>
          {call.error && (
            <div className="min-w-0">
              <dt className="text-rose-500">Error</dt>
              <dd className="truncate text-rose-700">{call.error}</dd>
            </div>
          )}
        </dl>
      )}
    </a>
  );
}

export default function Wall({ calls }) {
  const now = useNow();
  const [showCompleted, setShowCompleted] = useState(false);

  const todayStart = new Date(now * 1000);
  todayStart.setHours(0, 0, 0, 0);
  const visibleCalls = [...calls.values()]
    .filter((call) => !call.ended_at || (showCompleted && call.ended_at >= todayStart.getTime() / 1000))
    .sort((a, b) => {
      if (Boolean(a.ended_at) !== Boolean(b.ended_at)) return a.ended_at ? 1 : -1;
      return b.started_at - a.started_at;
    });
  const activeCount = visibleCalls.filter((call) => !call.ended_at).length;
  const dense = visibleCalls.length > 6;

  return (
    <main className="mx-auto max-w-[1500px] space-y-10 px-5 py-8 sm:px-8 lg:px-10 lg:py-10">
      <WallOverview calls={calls} now={now} />

      <section aria-labelledby="active-calls-title">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <h2 id="active-calls-title" className="text-xl font-semibold tracking-tight text-slate-950">
            {showCompleted ? "Today's calls" : "Active calls"}
          </h2>
          <div className="flex items-center gap-3">
            <p className="text-sm font-medium text-slate-400">
              {activeCount ? `${activeCount} active` : "None active"}
            </p>
            <button
              type="button"
              aria-pressed={showCompleted}
              onClick={() => setShowCompleted((current) => !current)}
              className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold shadow-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 ${
                showCompleted
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
              }`}
            >
              Show completed
              <span className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${showCompleted ? "bg-emerald-500" : "bg-slate-200"}`}>
                <span className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${showCompleted ? "translate-x-4" : "translate-x-0"}`} />
              </span>
            </button>
          </div>
        </div>

        {visibleCalls.length === 0 ? (
          <div className="rounded-[22px] border border-dashed border-slate-300 bg-white/55 py-14 text-center">
            <p className="text-slate-400">The next call will appear here when it arrives.</p>
          </div>
        ) : (
          <div
            className={`grid gap-4 ${
              dense
                ? "grid-cols-[repeat(auto-fill,minmax(230px,1fr))]"
                : "grid-cols-[repeat(auto-fill,minmax(320px,1fr))]"
            }`}
          >
            {visibleCalls.map((call) => (
              <CallCard key={call.call_id} call={call} now={now} dense={dense} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
