const WINDOW_MINUTES = 60;
const BUCKET_MINUTES = 5;
const BUCKET_COUNT = WINDOW_MINUTES / BUCKET_MINUTES;

const OUTCOMES = [
  { key: "BOOK", label: "Bookings", color: "bg-emerald-500" },
  { key: "RESCHEDULE", label: "Rescheduled", color: "bg-sky-500" },
  { key: "CANCEL", label: "Cancellations", color: "bg-violet-500" },
  { key: "REGISTER", label: "Registrations", color: "bg-amber-400" },
];

function startOfToday(now) {
  const start = new Date(now * 1000);
  start.setHours(0, 0, 0, 0);
  return start.getTime() / 1000;
}

function hourlyBuckets(calls, now) {
  const bucketSeconds = BUCKET_MINUTES * 60;
  const windowStart = now - WINDOW_MINUTES * 60;
  const buckets = Array(BUCKET_COUNT).fill(0);

  for (const call of calls) {
    if (call.started_at < windowStart || call.started_at > now) continue;
    const index = Math.min(
      BUCKET_COUNT - 1,
      Math.floor((call.started_at - windowStart) / bucketSeconds),
    );
    buckets[index] += 1;
  }

  return buckets;
}

function formatClock(timestamp) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function formatToday(now) {
  const formatted = new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date(now * 1000));
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

function CallRhythm({ calls, now }) {
  const buckets = hourlyBuckets(calls, now);
  const peak = Math.max(...buckets, 1);
  const total = buckets.reduce((sum, value) => sum + value, 0);

  return (
    <section className="flex min-h-[300px] flex-col rounded-[22px] border border-slate-200/80 bg-white p-7 shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-slate-950">Call activity</h2>
          <p className="mt-1 text-sm text-slate-400">Activity during the last 60 minutes</p>
        </div>
        <div className="rounded-full bg-emerald-50 px-3 py-1.5 text-sm font-semibold text-emerald-700">
          {total} {total === 1 ? "call" : "calls"}
        </div>
      </div>

      <div className="mt-8 flex min-h-36 flex-1 items-end gap-2" aria-label={`${total} calls in the last hour`}>
        {buckets.map((value, index) => (
          <div key={index} className="relative flex h-full flex-1 items-end" title={`${value} calls`}>
            <div
              className={`w-full rounded-t-md ${index === buckets.length - 1 ? "bg-emerald-500" : "bg-emerald-100"}`}
              style={{ height: `${Math.max(5, (value / peak) * 100)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-between text-xs tabular-nums text-slate-400">
        <span>{formatClock(now - WINDOW_MINUTES * 60)}</span>
        <span>Now</span>
      </div>
    </section>
  );
}

function OutcomeSummary({ completedCalls, needsAttention }) {
  const counts = Object.fromEntries(OUTCOMES.map(({ key }) => [key, 0]));
  for (const call of completedCalls) {
    if (call.outcome in counts) counts[call.outcome] += 1;
  }
  const trackedTotal = Object.values(counts).reduce((sum, value) => sum + value, 0);

  return (
    <section className="flex min-h-[300px] flex-col rounded-[22px] border border-slate-200/80 bg-white p-7 shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-slate-950">Today's outcomes</h2>
          <p className="mt-1 text-sm text-slate-400">Completed actions</p>
        </div>
        <span className={`h-3 w-3 rounded-full ${needsAttention ? "bg-rose-500" : "bg-emerald-400"}`} />
      </div>

      <div className="mt-8 space-y-5">
        {OUTCOMES.map(({ key, label, color }) => {
          const count = counts[key];
          const width = trackedTotal ? (count / trackedTotal) * 100 : 0;
          return (
            <div key={key}>
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="font-medium text-slate-600">{label}</span>
                <span className="font-semibold tabular-nums text-slate-900">{count}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                <div className={`h-full rounded-full ${color}`} style={{ width: `${width}%` }} />
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-slate-100 pt-5 text-sm">
        <span className="text-slate-400">Human review</span>
        <span className={`font-semibold ${needsAttention ? "text-rose-600" : "text-emerald-700"}`}>
          {needsAttention ? `${needsAttention} pending` : "All clear"}
        </span>
      </div>
    </section>
  );
}

export default function WallOverview({ calls, now }) {
  const allCalls = [...calls.values()];
  const todayStart = startOfToday(now);
  const today = allCalls.filter((call) => call.started_at >= todayStart);
  const live = today.filter((call) => !call.ended_at);
  const completedCalls = today.filter((call) => call.ended_at);
  const needsAttention = live.filter((call) => {
    if (typeof call.score_overall !== "number" || call.score_overall >= 40) return false;
    try {
      return JSON.parse(call.score_json)?.turn_count >= 2;
    } catch {
      return false;
    }
  }).length;

  return (
    <div>
      <header className="mb-8 flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="text-sm font-medium text-emerald-700">{formatToday(now)}</p>
          <h1 className="mt-2 text-4xl font-semibold tracking-[-0.045em] text-slate-950 sm:text-5xl">
            Reception overview
          </h1>
        </div>
        <p className="max-w-xs text-sm leading-6 text-slate-500">
          {needsAttention
            ? `${needsAttention} call${needsAttention === 1 ? " needs" : "s need"} attention.`
            : live.length
              ? "The shift is running without alerts."
              : "Everything is ready for the next call."}
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1.65fr_1fr]">
        <CallRhythm calls={allCalls} now={now} />
        <OutcomeSummary completedCalls={completedCalls} needsAttention={needsAttention} />
      </div>
    </div>
  );
}
