import { useEffect, useMemo, useState } from "react";

import OutcomeHistogram from "./OutcomeHistogram.jsx";
import { INSURERS, OUTCOMES, REASONS, outcomeStyle } from "./outcomes.js";

const SEARCH_DEBOUNCE_MS = 250;

const timeFormat = new Intl.DateTimeFormat("es-ES", {
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
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 font-mono text-2xl tabular-nums text-slate-100">
        {value}
        {unit && <span className="ml-1 text-sm text-slate-500">{unit}</span>}
      </p>
      {hint && <p className="mt-0.5 text-xs text-slate-600">{hint}</p>}
    </div>
  );
}

function OutcomeCell({ call }) {
  if (!call.outcome) {
    return <span className="text-slate-600">—</span>;
  }
  const style = outcomeStyle(call.outcome);
  return (
    <span className="inline-flex items-center gap-1.5">
      <span style={{ backgroundColor: style.color }} className="h-2 w-2 rounded-full" />
      <span className="text-slate-200">{style.label}</span>
      {call.reason && <span className="font-mono text-xs text-slate-500">{call.reason}</span>}
    </span>
  );
}

function ScoringCell({ call }) {
  if (call.score_overall != null) {
    return (
      <span className="inline-block rounded-full bg-slate-800 px-2 py-0.5 font-mono text-xs tabular-nums text-slate-200">
        {Math.round(call.score_overall)}
      </span>
    );
  }
  if (call.score_error) {
    return <span className="font-mono text-xs text-rose-400">error</span>;
  }
  return <span className="text-slate-600">—</span>;
}

export default function History() {
  const [calls, setCalls] = useState([]);
  const [stats, setStats] = useState(null);
  const [histogram, setHistogram] = useState(null);
  const [filters, setFilters] = useState({
    q: "",
    outcome: "",
    reason: "",
    name: "",
    insurer: "",
    date_from: "",
    date_to: "",
  });
  const [query, setQuery] = useState("");
  const [nameQuery, setNameQuery] = useState("");
  const [loading, setLoading] = useState(true);

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
    Promise.all([
      fetch(`/api/calls?${params}`).then((r) => r.json()),
      fetch("/api/stats").then((r) => r.json()),
      // The histogram reads the same filters, so the picture and the table
      // can never disagree about what is being looked at.
      fetch(`/api/histogram?${params}`).then((r) => r.json()),
    ])
      .then(([callsBody, statsBody, histogramBody]) => {
        if (stale) return;
        setCalls(callsBody.calls);
        setStats(statsBody);
        setHistogram(histogramBody);
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
    <div className="space-y-4 p-6">
      {stats && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <StatTile label="Llamadas" value={stats.calls} hint={`${stats.live} en curso`} />
            <StatTile
              label="Primera voz p50"
              value={stats.ttfa_p50?.toFixed(2) ?? "—"}
              unit="s"
              hint="silencio = caso fallado"
            />
            <StatTile label="Primera voz p95" value={stats.ttfa_p95?.toFixed(2) ?? "—"} unit="s" />
            <StatTile
              label="Coste"
              value={stats.total_cost_eur?.toFixed(2) ?? "—"}
              unit="€"
              hint={
                stats.avg_cost_eur ? `${stats.avg_cost_eur.toFixed(4)} € por llamada` : "sin medir"
              }
            />
            <StatTile
              label="Con error"
              value={stats.failed}
              hint={stats.calls ? `${Math.round((stats.failed / stats.calls) * 100)}%` : ""}
            />
          </div>
          <OutcomeHistogram histogram={histogram} />
        </>
      )}

      <div className="flex flex-wrap gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Buscar en las transcripciones…"
          className="min-w-64 flex-1 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-slate-600 focus:outline-none"
        />
        <select
          value={filters.outcome}
          onChange={(event) =>
            setFilters((previous) => ({ ...previous, outcome: event.target.value }))
          }
          className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-sm text-slate-200"
        >
          <option value="">Cualquier resultado</option>
          {Object.entries(OUTCOMES).map(([verb, { label }]) => (
            <option key={verb} value={verb}>
              {label}
            </option>
          ))}
        </select>
        <input
          value={nameQuery}
          onChange={(event) => setNameQuery(event.target.value)}
          placeholder="Nombre del paciente"
          className="w-56 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-slate-600 focus:outline-none"
        />
        <select
          value={filters.insurer}
          onChange={(event) =>
            setFilters((previous) => ({ ...previous, insurer: event.target.value }))
          }
          className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 font-mono text-xs text-slate-200"
        >
          <option value="">Cualquier seguro</option>
          {INSURERS.map((insurer) => (
            <option key={insurer} value={insurer}>
              {insurer}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs text-slate-500">
          desde
          <input
            type="date"
            value={filters.date_from}
            onChange={(event) =>
              setFilters((previous) => ({ ...previous, date_from: event.target.value }))
            }
            className="bg-transparent font-mono text-slate-200 focus:outline-none"
          />
        </label>
        <label className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs text-slate-500">
          hasta
          <input
            type="date"
            value={filters.date_to}
            onChange={(event) =>
              setFilters((previous) => ({ ...previous, date_to: event.target.value }))
            }
            className="bg-transparent font-mono text-slate-200 focus:outline-none"
          />
        </label>
        <select
          value={filters.reason}
          onChange={(event) =>
            setFilters((previous) => ({ ...previous, reason: event.target.value }))
          }
          className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 font-mono text-xs text-slate-200"
        >
          <option value="">Cualquier motivo</option>
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
              setFilters({
                q: "",
                outcome: "",
                reason: "",
                name: "",
                insurer: "",
                date_from: "",
                date_to: "",
              });
            }}
            className="rounded-lg border border-slate-800 px-3 py-2 text-sm text-slate-400 hover:text-slate-200"
          >
            Limpiar
          </button>
        )}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900/80 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2 font-medium">Hora</th>
              <th className="px-4 py-2 font-medium">Duración</th>
              <th className="px-4 py-2 font-medium">Paciente</th>
              <th className="px-4 py-2 font-medium">Resultado</th>
              <th className="px-4 py-2 font-medium">Score</th>
              <th className="px-4 py-2 font-medium">Primera voz</th>
              <th className="px-4 py-2 font-medium">Coste</th>
              <th className="px-4 py-2 font-medium">Seguro</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/70">
            {calls.map((call) => (
              <tr
                key={call.call_id}
                onClick={() => {
                  window.location.hash = `#/call/${call.call_id}`;
                }}
                className="cursor-pointer hover:bg-slate-900/50"
              >
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs text-slate-400">
                  {timeFormat.format(new Date(call.started_at * 1000))}
                </td>
                <td className="px-4 py-2 font-mono tabular-nums text-slate-300">
                  {formatDuration(call.ended_at ? call.ended_at - call.started_at : null)}
                </td>
                <td className="px-4 py-2">
                  {call.patient_name ? (
                    <>
                      <span className="text-slate-200">{call.patient_name}</span>
                      <span className="ml-2 font-mono text-xs text-slate-600">
                        {call.patient_id}
                      </span>
                    </>
                  ) : (
                    <span className="text-slate-600">sin identificar</span>
                  )}
                </td>
                <td className="px-4 py-2">
                  <OutcomeCell call={call} />
                  {call.error && (
                    <p className="mt-0.5 truncate text-xs text-rose-400">{call.error}</p>
                  )}
                </td>
                <td className="px-4 py-2">
                  <ScoringCell call={call} />
                </td>
                <td className="px-4 py-2 font-mono tabular-nums text-slate-400">
                  {call.ttfa_seconds == null ? "—" : `${call.ttfa_seconds.toFixed(2)}s`}
                </td>
                <td className="px-4 py-2 font-mono tabular-nums text-slate-400">
                  {call.cost_eur == null ? "—" : `${call.cost_eur.toFixed(4)} €`}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-slate-500">
                  {call.insurer ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {calls.length === 0 && (
          <p className="px-4 py-12 text-center text-slate-600">
            {loading ? "Cargando…" : "Ninguna llamada coincide con el filtro."}
          </p>
        )}
      </div>
    </div>
  );
}
