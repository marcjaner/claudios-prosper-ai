import { useEffect, useRef, useState } from "react";

const DEFAULT_RULES = [
  ["Stay on scheduling", "Stay focused on appointment booking, rescheduling, cancellation, and clinic scheduling questions."],
  ["No medical advice", "Do not provide medical advice or diagnose symptoms; recommend contacting a healthcare professional when needed."],
  ["Protect privacy", "Protect patient privacy and verify identity before sharing patient information."],
  ["Use real availability", "Only offer appointment times returned by the clinic system."],
  ["Escalate when needed", "Escalate urgent, unsafe, or out-of-scope requests to a human."],
];

export default function Guardrails() {
  const [guardrails, setGuardrails] = useState([]);
  const [saved, setSaved] = useState(true);
  const [error, setError] = useState("");
  const syncTimer = useRef(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const response = await fetch("/api/guardrails");
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!cancelled) setGuardrails(data.guardrails.length ? data.guardrails.map((item) => ({ title: item.title, description: item.description })) : DEFAULT_RULES.map(([title, description]) => ({ title, description })));
      } catch {
        if (cancelled) return;
        setError("Safety rules could not be loaded. Retrying…");
        setTimeout(load, 1000);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  async function sync(next) {
    setGuardrails(next);
    setSaved(false);
    setError("");
    clearTimeout(syncTimer.current);
    syncTimer.current = setTimeout(async () => {
      try {
        const response = await fetch("/api/guardrails", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ guardrails: next }),
        });
        if (!response.ok) throw new Error();
        setSaved(true);
      } catch {
        setError("The change could not be saved.");
      }
    }, 300);
  }

  return (
    <main className="clinic-page max-w-4xl">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-700">Configuration</p>
          <h2 className="text-3xl font-semibold tracking-[-0.04em] text-slate-950">Rules</h2>
          <p className="mt-2 text-sm text-slate-500">Rules the receptionist must follow on every call.</p>
        </div>
        <span className={`rounded-full px-3 py-1.5 text-xs font-semibold ${saved ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
          {saved ? "Synced" : "Saving…"}
        </span>
      </div>
      <div className="space-y-3">
        {guardrails.map((rule, index) => (
          <div key={index} className="clinic-panel flex items-start gap-3 p-4 sm:p-5">
            <span className="mt-3 w-6 text-center font-mono text-xs tabular-nums text-slate-400">{String(index + 1).padStart(2, "0")}</span>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <textarea
                value={rule.title}
                onChange={(event) => {
                  const next = [...guardrails];
                  next[index] = { ...next[index], title: event.target.value };
                  sync(next);
                }}
                maxLength={80}
                rows={1}
                placeholder="Title"
                aria-label="Safety rule title"
                className="clinic-control min-h-12 w-full resize-y font-medium"
              />
              <textarea
                value={rule.description}
                onChange={(event) => { const next = [...guardrails]; next[index] = { ...next[index], description: event.target.value }; sync(next); }}
                rows={2}
                placeholder="Description"
                aria-label="Safety rule description"
                className="clinic-control min-h-16 w-full resize-y"
              />
            </div>
            <button type="button" onClick={() => sync(guardrails.filter((_, item) => item !== index))} className="mt-1 rounded-lg px-2 py-1 text-xl leading-none text-slate-400 transition-colors hover:bg-rose-50 hover:text-rose-600 focus-visible:outline-2 focus-visible:outline-rose-400" aria-label="Delete rule">×</button>
          </div>
        ))}
      </div>
      <button type="button" onClick={() => sync([...guardrails, { title: "", description: "" }])} className="mt-5 rounded-xl border border-dashed border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-500 transition-colors hover:border-emerald-400 hover:text-emerald-700 focus-visible:outline-2 focus-visible:outline-emerald-400">+ Add rule</button>
      {error && <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p>}
    </main>
  );
}
