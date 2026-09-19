import { useEffect, useState } from "react";

// Every call is cut off at three minutes, so duration is a countdown.
const CALL_LIMIT_SECONDS = 180;
const WARN_SECONDS = 150;
const CRITICAL_SECONDS = 170;
// An ended call stays on the wall briefly so you can read how it finished.
const LINGER_SECONDS = 10;

const STATE_LABELS = {
  connected: "conectando",
  speaking: "hablando",
  listening: "escuchando",
  thinking: "pensando",
  ended: "terminada",
  lost: "perdida",
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
  if (seconds >= CRITICAL_SECONDS) return "text-rose-400";
  if (seconds >= WARN_SECONDS) return "text-amber-400";
  return "text-emerald-400";
}

function DurationRing({ seconds, live }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.min(1, Math.max(0, seconds / CALL_LIMIT_SECONDS));
  return (
    <div className="relative h-16 w-16 shrink-0">
      <svg className="h-full w-full -rotate-90" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r={radius} fill="none" strokeWidth="5" className="stroke-white/10" />
        <circle
          cx="32"
          cy="32"
          r={radius}
          fill="none"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - progress)}
          className={`${live ? ringColor(seconds) : "text-slate-600"} stroke-current transition-[stroke-dashoffset] duration-300`}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center font-mono text-sm tabular-nums">
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
  let violations = [];
  try { violations = JSON.parse(call.guardrail_violations || "[]"); } catch { /* ignore malformed legacy data */ }
  const alert = live && turnCount >= 2 && hasScore && call.score_overall < 40;

  return (
    <a
      href={`#/call/${call.call_id}`}
      className={`block rounded-xl border p-4 transition-colors hover:border-slate-600 ${
        alert
          ? "border-rose-500 bg-rose-950/40 ring-1 ring-rose-500/50"
          : `bg-slate-900/70 ${live ? "border-slate-700" : "border-slate-800 opacity-60"}`
      }`}
    >
      <div className="flex items-start gap-4">
        <DurationRing seconds={seconds} live={live} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-slate-100">
            {call.patient_id ?? "sin identificar"}
          </p>
          <p className="truncate font-mono text-xs text-slate-500">
            {call.from_number ?? "número oculto"}
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            {call.guardrail_breached && <span title="Safety rule breached" className="text-amber-400">⚠ {violations.map((item) => item.guardrail).join(" · ")}</span>}
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                live ? "animate-pulse bg-emerald-400" : "bg-slate-600"
              }`}
            />
            <span className="text-slate-300">{state}</span>
            {alert ? (
              <span className="inline-flex animate-pulse items-center gap-1 rounded-full border border-rose-500/60 bg-rose-500/20 px-2 py-0.5 font-mono text-xs font-medium text-rose-300">
                ALERTA · SCORE {Math.round(call.score_overall)}
              </span>
            ) : (
              live &&
              hasScore && (
                <span className="inline-flex items-center rounded-full bg-slate-800 px-2 py-0.5 font-mono text-xs tabular-nums text-slate-300">
                  Score {Math.round(call.score_overall)}
                </span>
              )
            )}
          </p>
        </div>
      </div>
      {!dense && (
        <dl className="mt-3 flex gap-4 border-t border-slate-800 pt-3 text-xs text-slate-500">
          <div>
            <dt className="uppercase tracking-wide">primera voz</dt>
            <dd className="font-mono text-slate-300">
              {call.ttfa_seconds == null ? "—" : `${call.ttfa_seconds.toFixed(2)}s`}
            </dd>
          </div>
          {call.error && (
            <div className="min-w-0">
              <dt className="uppercase tracking-wide text-rose-400">error</dt>
              <dd className="truncate text-rose-300">{call.error}</dd>
            </div>
          )}
        </dl>
      )}
    </a>
  );
}

export default function Wall({ calls }) {
  const now = useNow();

  const onWall = [...calls.values()]
    .filter((call) => !call.ended_at || now - call.ended_at < LINGER_SECONDS)
    .sort((a, b) => b.started_at - a.started_at);
  const dense = onWall.length > 6;

  return (
    <main className="p-6">
      {onWall.length === 0 ? (
        <p className="py-24 text-center text-slate-600">
          Sin llamadas. El wall se llena solo cuando entre la primera.
        </p>
      ) : (
        <div
          className={`grid gap-4 ${
            dense
              ? "grid-cols-[repeat(auto-fill,minmax(230px,1fr))]"
              : "grid-cols-[repeat(auto-fill,minmax(320px,1fr))]"
          }`}
        >
          {onWall.map((call) => (
            <CallCard key={call.call_id} call={call} now={now} dense={dense} />
          ))}
        </div>
      )}
    </main>
  );
}
