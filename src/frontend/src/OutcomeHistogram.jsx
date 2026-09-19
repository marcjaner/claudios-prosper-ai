import { useState } from "react";

import { GROUPS, HISTORY_RANGES, OUTCOMES } from "./outcomes.js";

// Stacked in the order a call escalates away from the goal, so the healthy
// mass sits on the baseline and trouble accumulates on top.
const ORDER = ["wrote", "closed", "absent"];
const SERIES = ORDER.flatMap((group) => group === "absent"
  ? [{ key: "absent", ...GROUPS.absent }]
  : Object.entries(OUTCOMES)
      .filter(([, outcome]) => outcome.group === group)
      .map(([key, outcome]) => ({ key, label: outcome.label, color: outcome.chartColor })),
);
const VOLUME_COLOR = GROUPS.wrote.color;

function bucketFormat(bucketSeconds) {
  // Bars narrower than a day are a clock; wider ones are a date.
  const options =
    bucketSeconds >= 24 * 3600
      ? { day: "2-digit", month: "short" }
      : { hour: "2-digit", minute: "2-digit" };
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "Europe/Madrid" });
}

function seriesCount(bucket, key) {
  return key === "absent" ? bucket.absent : bucket.outcomes?.[key] ?? 0;
}

export default function OutcomeHistogram({ histogram, range, onRangeChange, loading, error }) {
  const [hovered, setHovered] = useState(null);
  const [stacked, setStacked] = useState(false);
  const buckets = histogram?.buckets ?? [];
  const bucketSeconds = histogram?.bucket_seconds ?? 86400;
  const format = bucketFormat(bucketSeconds);
  const dateFormat = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "short", year: "numeric", timeZone: "Europe/Madrid",
  });
  const rangeFormat = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium", ...(bucketSeconds < 86400 ? { timeStyle: "short" } : {}), timeZone: "Europe/Madrid",
  });
  const total = buckets.reduce((sum, bucket) => sum + bucket.total, 0);
  const peak = Math.max(1, ...buckets.map((bucket) => bucket.total));
  const axisMax = Math.ceil(peak / 2) * 2;
  const active = buckets.find((bucket) => bucket.bucket === hovered);
  const interval = bucketSeconds >= 86400
    ? `${bucketSeconds / 86400}-day`
    : bucketSeconds >= 3600 ? `${bucketSeconds / 3600}-hour` : `${bucketSeconds / 60}-minute`;
  const labelIndices = new Set(Array.from({ length: Math.min(5, buckets.length) }, (_, index) =>
    Math.round(index * (buckets.length - 1) / Math.max(1, Math.min(5, buckets.length) - 1)),
  ));
  const bucketLabel = (bucket) => `${dateFormat.format(new Date(bucket.bucket * 1000))}${bucketSeconds < 86400 ? ` · ${format.format(new Date(bucket.bucket * 1000))}` : ""}`;
  const summary = (bucket) => `${bucketLabel(bucket)} · ${bucket.total} calls${stacked
    ? ` · ${SERIES.map(({ key, label }) => `${label}: ${seriesCount(bucket, key)}`).join(", ")}` : ""}`;

  return (
    <section aria-label="Call volume over time" aria-busy={loading} className="clinic-panel p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold text-slate-900">Calls over time</h2>
          <p className="mt-1 text-sm text-slate-500">
            <span className="font-semibold tabular-nums text-slate-900">{total}</span> completed calls
            <span className="mx-2 text-slate-300">·</span>{interval} intervals
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            role="switch"
            aria-checked={stacked}
            onClick={() => setStacked((current) => !current)}
            className="inline-flex items-center gap-2 rounded-lg py-2 text-xs font-medium text-slate-600 transition-colors hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-emerald-600"
          >
            <span aria-hidden="true" className={`relative h-5 w-9 rounded-full transition-colors ${stacked ? "bg-emerald-600" : "bg-slate-200"}`}>
              <span className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${stacked ? "translate-x-4" : ""}`} />
            </span>
            Break down by action
          </button>
          <select aria-label="Call history time range" value={range} onChange={(event) => onRangeChange(event.target.value)} className="clinic-control">
            {HISTORY_RANGES.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
      </div>

      {error ? (
        <p role="alert" className="py-16 text-center text-sm text-rose-700">{error}</p>
      ) : loading ? (
        <div role="status" className="mt-6 flex h-48 items-center justify-center rounded-lg bg-slate-50 text-sm text-slate-500">Loading call volume…</div>
      ) : (
        <>
          <div className="mt-6 grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2">
            <div aria-hidden="true" className="relative h-48 text-right text-[11px] tabular-nums text-slate-400">
              {[axisMax, axisMax / 2, 0].map((value, index) => (
                <span key={value} className="absolute right-0 -translate-y-1/2" style={{ top: `${index * 50}%` }}>{value}</span>
              ))}
            </div>
            <div className="relative h-48" onMouseLeave={() => setHovered(null)}>
              <div aria-hidden="true" className="pointer-events-none absolute inset-0 flex flex-col justify-between">
                {[0, 1, 2].map((line) => <div key={line} className="border-t border-slate-100" />)}
              </div>
              <div className="relative flex h-full gap-0.5">
                {buckets.map((bucket) => (
                  <button
                    key={bucket.bucket}
                    type="button"
                    aria-label={summary(bucket)}
                    title={summary(bucket)}
                    onMouseEnter={() => setHovered(bucket.bucket)}
                    onFocus={() => setHovered(bucket.bucket)}
                    onBlur={() => setHovered(null)}
                    onClick={() => setHovered(bucket.bucket)}
                    className={`flex h-full min-w-0 flex-1 items-end rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 ${active && active.bucket !== bucket.bucket ? "opacity-40" : ""}`}
                  >
                    <span
                      style={{ height: `${(bucket.total / axisMax) * 100}%`, backgroundColor: stacked ? undefined : VOLUME_COLOR }}
                      // A hit target the full height of the plot, not just the bar.
                      // overflow-hidden on the column, not first:rounded-t on a
                      // segment: flex-col-reverse puts the DOM-first child at the
                      // bottom, so that would round the wrong end.
                      className="flex w-full flex-col-reverse overflow-hidden rounded-t-sm"
                    >
                      {stacked && SERIES.map(({ key, color }) => seriesCount(bucket, key) > 0 && (
                        <span key={key} style={{ height: `${(seriesCount(bucket, key) / bucket.total) * 100}%`, backgroundColor: color }} className="w-full shrink-0" />
                      ))}
                    </span>
                  </button>
                ))}
              </div>
              {total === 0 && <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-slate-400">No calls in this period</p>}
            </div>
            <div />
            <div aria-hidden="true" className="relative mt-3 h-5 text-[10px] text-slate-400 sm:text-xs">
              {buckets.map((bucket, index) => labelIndices.has(index) && (
                <span key={bucket.bucket} className="absolute whitespace-nowrap" style={{
                  left: `${((index + 0.5) / buckets.length) * 100}%`,
                  transform: index === 0 ? "none" : index === buckets.length - 1 ? "translateX(-100%)" : "translateX(-50%)",
                }}>{format.format(new Date(bucket.bucket * 1000))}</span>
              ))}
            </div>
          </div>
          <p className="mt-4 min-h-5 text-xs tabular-nums text-slate-500">
            {active ? `${bucketLabel(active)} · ${active.total} calls`
              : buckets.length ? `${rangeFormat.format(new Date((histogram.range_start ?? buckets[0].bucket) * 1000))} – ${rangeFormat.format(new Date((histogram.range_end ?? buckets.at(-1).bucket) * 1000))} · Europe/Madrid`
                : "Choose a date range to explore call volume."}
          </p>
          <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-2 border-t border-slate-100 pt-4">
            {(stacked ? SERIES : [{ key: "total", label: "Calls", color: VOLUME_COLOR }]).map(({ key, label, color }) => (
              <li key={key} className="flex items-center gap-1.5 text-xs">
                <span style={{ backgroundColor: color }} className="h-2 w-2 rounded-sm" />
                <span className="text-slate-500">{label}</span>
                <span className="font-semibold tabular-nums text-slate-800">
                  {key === "total" ? active?.total ?? total : active ? seriesCount(active, key) : buckets.reduce((sum, bucket) => sum + seriesCount(bucket, key), 0)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
