import { useState } from "react";

import { GROUPS } from "./outcomes.js";

// Stacked in the order a call escalates away from the goal, so the healthy
// mass sits on the baseline and trouble accumulates on top.
const ORDER = ["wrote", "closed", "absent"];
const MIN_BAR_HEIGHT = 2;

function bucketFormat(bucketSeconds) {
  // Bars narrower than a day are a clock; wider ones are a date.
  const options =
    bucketSeconds >= 24 * 3600
      ? { day: "2-digit", month: "short" }
      : { hour: "2-digit", minute: "2-digit" };
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "Europe/Madrid" });
}

export default function OutcomeHistogram({ histogram }) {
  const [hovered, setHovered] = useState(null);
  const buckets = histogram?.buckets ?? [];

  if (buckets.length === 0) return null;

  const format = bucketFormat(histogram.bucket_seconds);
  const totals = Object.fromEntries(
    ORDER.map((key) => [key, buckets.reduce((sum, bucket) => sum + bucket[key], 0)]),
  );
  const peak = Math.max(
    1,
    ...buckets.map((bucket) => ORDER.reduce((sum, key) => sum + bucket[key], 0)),
  );

  const active = hovered == null ? null : buckets[hovered];

  return (
    <div className="clinic-panel p-6">
      <div className="flex flex-wrap items-baseline gap-x-4">
        <p className="font-semibold text-slate-900">
          Calls over time
        </p>
        <p className="text-xs text-slate-400">
          {active
            ? `${format.format(new Date(active.bucket * 1000))} · ${
                ORDER.reduce((sum, key) => sum + active[key], 0)
              } calls`
            : `${
                histogram.bucket_seconds >= 3600
                  ? `${histogram.bucket_seconds / 3600}-hour`
                  : `${histogram.bucket_seconds / 60}-minute`
              } intervals`}
        </p>
      </div>

      <div className="mt-3 flex h-32 items-end gap-px" onMouseLeave={() => setHovered(null)}>
        {buckets.map((bucket, index) => {
          const total = ORDER.reduce((sum, key) => sum + bucket[key], 0);
          return (
            <div
              key={bucket.bucket}
              onMouseEnter={() => setHovered(index)}
              style={{ height: `${(total / peak) * 100}%` }}
              // A hit target the full height of the plot, not just the bar.
              // overflow-hidden on the column, not first:rounded-t on a
              // segment: flex-col-reverse puts the DOM-first child at the
              // bottom, so that would round the wrong end.
              className={`relative flex min-w-1.5 flex-1 flex-col-reverse justify-start gap-0.5 overflow-hidden rounded-t transition-opacity ${
                hovered != null && hovered !== index ? "opacity-50" : ""
              }`}
            >
              {ORDER.map((key) =>
                bucket[key] > 0 ? (
                  <div
                    key={key}
                    style={{
                      height: `${Math.max(MIN_BAR_HEIGHT, (bucket[key] / total) * 100)}%`,
                      backgroundColor: GROUPS[key].color,
                    }}
                    className="w-full"
                  />
                ) : null,
              )}
            </div>
          );
        })}
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
        {ORDER.map((key) => (
          <li key={key} className="flex items-center gap-1.5 text-xs">
            <span
              style={{ backgroundColor: GROUPS[key].color }}
              className="h-2 w-2 rounded-full"
            />
            <span className="text-slate-500">{GROUPS[key].label}</span>
            <span className="font-semibold tabular-nums text-slate-800">
              {active ? active[key] : totals[key]}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-slate-400">
        A reasoned refusal scores like a booking; only a call without a record always fails.
      </p>
    </div>
  );
}
