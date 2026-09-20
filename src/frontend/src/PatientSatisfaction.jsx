const STATE_STYLES = {
  satisfied: {
    label: "Satisfied",
    dot: "bg-emerald-500",
    text: "text-emerald-700",
    fill: "#10b981",
  },
  neutral: {
    label: "Neutral",
    dot: "bg-slate-400",
    text: "text-slate-700",
    fill: "#64748b",
  },
  frustrated: {
    label: "Frustrated",
    dot: "bg-rose-500",
    text: "text-rose-700",
    fill: "#f43f5e",
  },
  insufficient_signal: {
    label: "Not enough signal",
    dot: "bg-slate-300",
    text: "text-slate-500",
    fill: "#cbd5e1",
  },
};

const TREND_STYLES = {
  improving: { label: "Improving", symbol: "↗", tone: "text-emerald-700" },
  stable: { label: "Stable", symbol: "→", tone: "text-slate-500" },
  declining: { label: "Declining", symbol: "↘", tone: "text-rose-700" },
};

const WIDTH = 540;
const HEIGHT = 126;
const LEFT = 66;
const RIGHT = 12;
const TOP = 8;
const BOTTOM = 24;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;

function derivedState(score, confidence) {
  if (confidence < 0.45) return "insufficient_signal";
  if (score >= 70) return "satisfied";
  if (score >= 40) return "neutral";
  return "frustrated";
}

export function satisfactionMeasurements(events) {
  return events
    .filter(({ kind, payload }) =>
      kind === "patient_satisfaction" && Number.isFinite(payload?.score),
    )
    .map((event) => ({
      ...event.payload,
      ts: event.ts,
      state: STATE_STYLES[event.payload.state]
        ? event.payload.state
        : derivedState(event.payload.score, event.payload.confidence ?? 0),
      trend: TREND_STYLES[event.payload.trend] ? event.payload.trend : "stable",
    }))
    .sort((a, b) => a.ts - b.ts);
}

function formatOffset(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function Summary({ measurement, compact = false }) {
  if (!measurement) {
    return (
      <div>
        <p className="text-sm font-semibold text-slate-500">Waiting for signal</p>
        {!compact && <p className="mt-0.5 text-xs text-slate-400">The estimate appears after the patient responds.</p>}
      </div>
    );
  }

  const state = STATE_STYLES[measurement.state];
  const trend = TREND_STYLES[measurement.trend];
  const showScore = measurement.state !== "insufficient_signal";
  const showTrend = showScore && measurement.sample_index > 1;
  return (
    <div className={`flex min-w-0 ${compact ? "items-center gap-2" : "items-end justify-between gap-4"}`}>
      <div className="flex min-w-0 items-center gap-2">
        <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${state.dot}`} />
        <p className={`truncate font-semibold ${compact ? "text-sm" : "text-lg"} ${state.text}`}>
          {state.label}{showScore ? ` · ${Math.round(measurement.score)}` : ""}
        </p>
      </div>
      {showTrend && (
        <p className={`shrink-0 text-xs font-semibold ${trend.tone}`}>
          <span aria-hidden="true">{trend.symbol}</span> {trend.label}
        </p>
      )}
    </div>
  );
}

function MiniTrend({ measurements }) {
  const reliable = measurements.filter(({ state }) => state !== "insufficient_signal");
  if (reliable.length < 2) return null;
  const points = reliable.slice(-8);
  const coordinates = points.map((measurement, index) => {
    const x = 2 + (index / (points.length - 1)) * 52;
    const y = 3 + ((100 - measurement.score) / 100) * 18;
    return `${x},${y}`;
  });
  const latest = points.at(-1);

  return (
    <svg aria-hidden="true" viewBox="0 0 56 24" className="h-6 w-14 shrink-0 overflow-visible">
      <polyline points={coordinates.join(" ")} fill="none" stroke="#94a3b8" strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx={coordinates.at(-1).split(",")[0]} cy={coordinates.at(-1).split(",")[1]} r="2.5" fill={STATE_STYLES[latest.state].fill} />
    </svg>
  );
}

export function PatientSatisfactionCard({ events }) {
  const measurements = satisfactionMeasurements(events);
  const latest = measurements.at(-1);

  return (
    <div className="mt-3 flex min-h-11 items-center justify-between gap-3 border-t border-slate-200/70 pt-3">
      <div className="min-w-0">
        <p className="mb-1 text-[11px] text-slate-400">Estimated patient experience</p>
        <Summary measurement={latest} compact />
      </div>
      <MiniTrend measurements={measurements} />
    </div>
  );
}

function SatisfactionTimeline({ measurements, startedAt, endedAt }) {
  const reliable = measurements.filter(({ state }) => state !== "insufficient_signal");
  if (!reliable.length) {
    return (
      <div className="mt-4 grid h-24 place-items-center rounded-xl border border-dashed border-slate-200 bg-white/60 px-5 text-center text-xs text-slate-400">
        The timeline will appear after the first estimate.
      </div>
    );
  }

  const lastTimestamp = Math.max(endedAt ?? 0, measurements.at(-1).ts);
  const duration = Math.max(1, lastTimestamp - startedAt);
  const x = (timestamp) => LEFT + ((timestamp - startedAt) / duration) * PLOT_WIDTH;
  const y = (score) => TOP + ((100 - score) / 100) * PLOT_HEIGHT;
  const points = reliable.map((measurement) => `${x(measurement.ts)},${y(measurement.score)}`);

  return (
    <svg
      role="img"
      aria-label={`Estimated patient satisfaction over ${formatOffset(duration)}`}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="mt-3 h-auto w-full"
    >
      <rect x={LEFT} y={TOP} width={PLOT_WIDTH} height={y(70) - TOP} rx="7" fill="#ecfdf5" />
      <rect x={LEFT} y={y(70)} width={PLOT_WIDTH} height={y(40) - y(70)} fill="#f8fafc" />
      <rect x={LEFT} y={y(40)} width={PLOT_WIDTH} height={TOP + PLOT_HEIGHT - y(40)} rx="7" fill="#fff1f2" />
      {[70, 40].map((score) => (
        <line key={score} x1={LEFT} x2={WIDTH - RIGHT} y1={y(score)} y2={y(score)} stroke="#cbd5e1" strokeDasharray="3 4" />
      ))}
      <text x="0" y={y(85) + 3} fill="#047857" fontSize="10">Satisfied</text>
      <text x="0" y={y(55) + 3} fill="#64748b" fontSize="10">Neutral</text>
      <text x="0" y={y(20) + 3} fill="#be123c" fontSize="10">Frustrated</text>
      {reliable.length > 1 && (
        <polyline points={points.join(" ")} fill="none" stroke="#64748b" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      )}
      {reliable.map((measurement, index) => (
        <circle
          key={`${measurement.ts}-${index}`}
          cx={x(measurement.ts)}
          cy={y(measurement.score)}
          r={index === reliable.length - 1 ? 4.5 : 3.5}
          fill={STATE_STYLES[measurement.state].fill}
          stroke="white"
          strokeWidth="2"
        >
          <title>{`${formatOffset(measurement.ts - startedAt)} · ${STATE_STYLES[measurement.state].label} · ${Math.round(measurement.score)} · ${Math.round((measurement.confidence ?? 0) * 100)}% confidence`}</title>
        </circle>
      ))}
      <text x={LEFT} y={HEIGHT - 4} fill="#94a3b8" fontSize="10">0:00</text>
      <text x={WIDTH - RIGHT} y={HEIGHT - 4} fill="#94a3b8" fontSize="10" textAnchor="end">{formatOffset(duration)}</text>
    </svg>
  );
}

export function PatientSatisfactionDetail({ events, startedAt, endedAt }) {
  const measurements = satisfactionMeasurements(events);
  const latest = measurements.at(-1);

  return (
    <section aria-label="Estimated patient experience" className="min-w-0 border-t border-slate-100 pt-4 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-0">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-slate-400">Estimated patient experience</p>
          <p className="mt-0.5 text-[11px] text-slate-400">Transcript-based, not a clinical signal</p>
        </div>
        {latest && latest.state !== "insufficient_signal" && (
          <span className="shrink-0 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500">
            {Math.round((latest.confidence ?? 0) * 100)}% confidence
          </span>
        )}
      </div>
      <div className="mt-2">
        <Summary measurement={latest} />
      </div>
      <SatisfactionTimeline measurements={measurements} startedAt={startedAt} endedAt={endedAt} />
      {latest?.reason && (
        <p className="mt-1 truncate text-[11px] text-slate-400" title={latest.reason}>{latest.reason}</p>
      )}
    </section>
  );
}
