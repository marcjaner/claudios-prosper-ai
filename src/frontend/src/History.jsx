import { useEffect, useMemo, useState } from "react";

import OutcomeHistogram from "./OutcomeHistogram.jsx";
import { historyDateRange, INSURERS, OUTCOMES, REASONS, outcomeStyle } from "./outcomes.js";

const SEARCH_DEBOUNCE_MS = 250;
const DEMO_STATS = {
  calls: 28,
  live: 4,
  total_cost_eur: 0.474,
  avg_cost_eur: 0.01975,
  success_pct: 91.7,
  bookings_pct: 62.5,
};
const DEMO_OUTCOMES = {
  BOOK: [1, 1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1],
  RESCHEDULE: [0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 1, 0],
  CANCEL: [0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0],
  REGISTER: [0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0],
  NO_ACTION: [0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 1],
  ESCALATE: [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1],
};

function buildDemoHistogram() {
  const bucketSeconds = 15 * 60;
  const start = Math.floor((Date.now() / 1000 - 3 * 3600) / bucketSeconds) * bucketSeconds;
  const buckets = Array.from({ length: 12 }, (_, index) => {
    const outcomes = Object.fromEntries(
      Object.entries(DEMO_OUTCOMES).map(([key, values]) => [key, values[index]]),
    );
    return {
      bucket: start + index * bucketSeconds,
      total: Object.values(outcomes).reduce((sum, value) => sum + value, 0),
      wrote: outcomes.BOOK + outcomes.RESCHEDULE + outcomes.CANCEL + outcomes.REGISTER,
      closed: outcomes.NO_ACTION + outcomes.ESCALATE,
      absent: 0,
      outcomes,
    };
  });
  return {
    bucket_seconds: bucketSeconds,
    range_start: buckets[0].bucket,
    range_end: buckets.at(-1).bucket,
    buckets,
  };
}

const timeFormat = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "short",
  timeStyle: "medium",
  timeZone: "Europe/Madrid",
});

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function StatTile({ label, value, unit, hint }) {
  return (
    <div className="clinic-panel px-5 py-4">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className="mt-3 text-3xl font-semibold tracking-[-0.04em] tabular-nums text-slate-950">
        {value}
        {unit && <span className="ml-1 text-sm font-medium text-slate-400">{unit}</span>}
      </p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

function OutcomeCell({ call }) {
  if (!call.outcome) {
    return <span className="text-slate-400">—</span>;
  }
  const style = outcomeStyle(call.outcome);
  return (
    <span className="inline-flex items-center gap-1.5">
      {Number(call.guardrail_breached) === 1 && <span title="Guardrail breached" className="text-amber-400">⚠</span>}
      <span style={{ backgroundColor: style.color }} className="h-2 w-2 rounded-full" />
      <span className="font-medium text-slate-700">{style.label}</span>
      {call.reason && <span className="text-xs text-slate-400">{call.reason}</span>}
    </span>
  );
}

export default function History({ calls: liveCalls }) {
  const [calls, setCalls] = useState([]);
  const [stats, setStats] = useState(null);
  const [histogram, setHistogram] = useState(null);
  const [range, setRange] = useState("6h");
  const [filters, setFilters] = useState(() => ({
    q: "",
    outcome: "",
    reason: "",
    name: "",
    insurer: "",
    ...historyDateRange("6h"),
  }));
  const [query, setQuery] = useState("");
  const [nameQuery, setNameQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const demo = useMemo(
    () => [...(liveCalls?.values() ?? [])].some((call) => call.call_id.startsWith("CAlive")),
    [liveCalls],
  );
  const demoHistogram = useMemo(buildDemoHistogram, []);
  const displayedStats = demo ? DEMO_STATS : stats;
  const displayedHistogram = demo ? demoHistogram : histogram;

  function changeRange(value) {
    setRange(value);
    setFilters((previous) => ({
      ...previous,
      ...(value === "custom"
        ? { started_after: "", started_before: "" }
        : historyDateRange(value)),
    }));
  }

  function changeDate(key, value) {
    setRange("custom");
    setFilters((previous) => ({ ...previous, started_after: "", started_before: "", [key]: value }));
  }

  useEffect(() => {
    const timer = setTimeout(
      () => setFilters((previous) => ({ ...previous, q: query, name: nameQuery })),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [query, nameQuery]);

  useEffect(() => {
    const params = new URLSearchParams([
      ...Object.entries(filters).filter(([, value]) => value),
      // A call still in flight belongs on the wall, not in the history table,
      // where it would sit with no duration and no outcome.
      ["ended_only", "true"],
    ]);
    let stale = false;
    setLoading(true);
    setError(null);
    setHistogram(null);
    if (filters.date_from && filters.date_to && filters.date_from > filters.date_to) {
      setError("Start date must be on or before end date.");
      setCalls([]);
      setLoading(false);
      return;
    }
    const load = (url) => fetch(url).then((response) => {
      if (!response.ok) throw new Error("Could not load call history. Please try again.");
      return response.json();
    });
    Promise.all([
      load(`/api/calls?${params}`),
      load("/api/stats"),
      // The histogram reads the same filters, so the picture and the table
      // can never disagree about what is being looked at.
      load(`/api/histogram?${params}`),
    ])
      .then(([callsBody, statsBody, histogramBody]) => {
        if (stale) return;
        setCalls(callsBody.calls);
        setStats(statsBody);
        setHistogram(histogramBody);
      })
      .catch(() => {
        if (stale) return;
        setCalls([]);
        setError("Could not load call history. Please try again.");
      })
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
  }, [filters]);

  const filtered = useMemo(
    () => Object.entries(filters).some(([, value]) => value),
    [filters],
  );

  return (
    <main className="clinic-page space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-emerald-700">Operations archive</p>
          <h1 className="mt-2 text-4xl font-semibold tracking-[-0.045em] text-slate-950 sm:text-5xl">
            Call history
          </h1>
        </div>
        <p className="max-w-sm text-sm leading-6 text-slate-500">
          Review completed conversations, outcomes, response time and cost.
        </p>
      </header>

      {displayedStats && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile label="Calls" value={displayedStats.calls} hint={`${displayedStats.live} active`} />
            <StatTile
              label="Cost"
              value={displayedStats.total_cost_eur?.toFixed(2) ?? "—"}
              unit="€"
              hint={
                displayedStats.avg_cost_eur ? `${displayedStats.avg_cost_eur.toFixed(4)} € per call` : "Not measured"
              }
            />
            <StatTile label="Success calls" value={`${displayedStats.success_pct.toFixed(1)}%`} hint="Resolved automatically" />
            <StatTile label="Bookings" value={`${displayedStats.bookings_pct.toFixed(1)}%`} hint="Of completed calls" />
          </div>
        </>
      )}

      <OutcomeHistogram histogram={displayedHistogram} range={range} onRangeChange={changeRange} loading={demo ? false : loading} error={demo ? null : error} />

      <section aria-label="History filters" className="clinic-panel flex flex-wrap gap-2 p-3">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search transcripts…"
          className="clinic-control min-w-64 flex-1"
        />
        <select
          value={filters.outcome}
          onChange={(event) =>
            setFilters((previous) => ({ ...previous, outcome: event.target.value }))
          }
          className="clinic-control"
        >
          <option value="">Any outcome</option>
          {Object.entries(OUTCOMES).map(([verb, { label }]) => (
            <option key={verb} value={verb}>
              {label}
            </option>
          ))}
        </select>
        <input
          value={nameQuery}
          onChange={(event) => setNameQuery(event.target.value)}
          placeholder="Patient name"
          className="clinic-control w-56"
        />
        <select
          value={filters.insurer}
          onChange={(event) =>
            setFilters((previous) => ({ ...previous, insurer: event.target.value }))
          }
          className="clinic-control"
        >
          <option value="">Any insurer</option>
          {INSURERS.map((insurer) => (
            <option key={insurer} value={insurer}>
              {insurer}
            </option>
          ))}
        </select>
        <label className="clinic-control flex items-center gap-2 text-xs text-slate-500">
          From
          <input
            type="date"
            value={filters.date_from}
            max={filters.date_to || undefined}
            onChange={(event) => changeDate("date_from", event.target.value)}
            className="bg-transparent text-slate-700 focus:outline-none"
          />
        </label>
        <label className="clinic-control flex items-center gap-2 text-xs text-slate-500">
          To
          <input
            type="date"
            value={filters.date_to}
            min={filters.date_from || undefined}
            onChange={(event) => changeDate("date_to", event.target.value)}
            className="bg-transparent text-slate-700 focus:outline-none"
          />
        </label>
        <select
          value={filters.reason}
          onChange={(event) =>
            setFilters((previous) => ({ ...previous, reason: event.target.value }))
          }
          className="clinic-control"
        >
          <option value="">Any reason</option>
          {REASONS.map((reason) => (
            <option key={reason} value={reason}>
              {reason}
            </option>
          ))}
        </select>
        {filtered && (
          <button
            onClick={() => {
              setQuery("");
              setNameQuery("");
              setRange("all");
              setFilters({
                q: "",
                outcome: "",
                reason: "",
                name: "",
                insurer: "",
                ...historyDateRange("all"),
              });
            }}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-500 hover:border-slate-300 hover:text-slate-800"
          >
            Clear
          </button>
        )}
      </section>

      <div className="clinic-panel overflow-x-auto">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="border-b border-slate-100 bg-slate-50/70 text-left text-xs text-slate-500">
            <tr>
              <th className="px-5 py-4 font-semibold">Time</th>
              <th className="px-5 py-4 font-semibold">Duration</th>
              <th className="px-5 py-4 font-semibold">Patient</th>
              <th className="px-5 py-4 font-semibold">Outcome</th>
              <th className="px-5 py-4 font-semibold">First response</th>
              <th className="px-5 py-4 font-semibold">Cost</th>
              <th className="px-5 py-4 font-semibold">Insurer</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {calls.map((call) => (
              <tr
                key={call.call_id}
                onClick={() => {
                  window.location.hash = `#/call/${call.call_id}?from=history`;
                }}
                className="cursor-pointer transition-colors hover:bg-emerald-50/50"
              >
                <td className="whitespace-nowrap px-5 py-4 text-xs tabular-nums text-slate-500">
                  {timeFormat.format(new Date(call.started_at * 1000))}
                </td>
                <td className="px-5 py-4 font-medium tabular-nums text-slate-700">
                  {formatDuration(call.ended_at ? call.ended_at - call.started_at : null)}
                </td>
                <td className="px-5 py-4">
                  {call.patient_name ? (
                    <>
                      <span className="font-medium text-slate-800">{call.patient_name}</span>
                      <span className="ml-2 text-xs text-slate-400">
                        {call.patient_id}
                      </span>
                    </>
                  ) : (
                    <span className="text-slate-400">Unidentified</span>
                  )}
                </td>
                <td className="px-5 py-4">
                  <OutcomeCell call={call} />
                  {call.error && (
                    <p className="mt-0.5 truncate text-xs text-rose-600">{call.error}</p>
                  )}
                </td>
                <td className="px-5 py-4 tabular-nums text-slate-500">
                  {call.ttfa_seconds == null ? "—" : `${call.ttfa_seconds.toFixed(2)}s`}
                </td>
                <td className="px-5 py-4 tabular-nums text-slate-500">
                  {call.cost_eur == null ? "—" : `${call.cost_eur.toFixed(4)} €`}
                </td>
                <td className="px-5 py-4 text-xs text-slate-500">
                  {call.insurer ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {calls.length === 0 && (
          <p className="px-4 py-12 text-center text-slate-400">
            {loading ? "Loading…" : "No calls match these filters."}
          </p>
        )}
      </div>
    </main>
  );
}
