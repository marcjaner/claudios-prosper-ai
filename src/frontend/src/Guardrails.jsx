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
    <main className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs uppercase tracking-[0.2em] text-emerald-400">Configuration</p>
          <h2 className="text-3xl font-semibold text-slate-100">Safety rules</h2>
          <p className="mt-2 text-sm text-slate-500">Rules the receptionist must follow on every call.</p>
        </div>
        <span className={`text-xs ${saved ? "text-emerald-400" : "text-amber-400"}`}>
          {saved ? "Synced" : "Saving…"}
        </span>
      </div>
      <div className="space-y-3">
        {guardrails.map((rule, index) => (
          <div key={index} className="flex items-start gap-3 rounded-xl border border-slate-800 bg-slate-900/70 p-3">
            <span className="mt-3 w-6 text-center font-mono text-xs text-slate-600">{index + 1}</span>
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
                className="min-h-12 resize-y rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm font-medium text-slate-200 outline-none focus:border-emerald-500"
              />
              <textarea
                value={rule.description}
                onChange={(event) => { const next = [...guardrails]; next[index] = { ...next[index], description: event.target.value }; sync(next); }}
                rows={2}
                placeholder="Description"
                aria-label="Safety rule description"
                className="min-h-12 resize-y rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none focus:border-emerald-500"
              />
            </div>
            <button onClick={() => sync(guardrails.filter((_, item) => item !== index))} className="mt-2 px-2 text-slate-500 hover:text-rose-400" aria-label="Delete rule">×</button>
          </div>
        ))}
      </div>
      <button onClick={() => sync([...guardrails, { title: "", description: "" }])} className="mt-5 rounded-lg border border-dashed border-slate-700 px-4 py-2 text-sm text-slate-400 hover:border-emerald-500 hover:text-emerald-400">+ Add safety rule</button>
      {error && <p className="mt-4 text-sm text-rose-400">{error}</p>}
    </main>
  );
}
