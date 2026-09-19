import { useEffect, useState } from "react";

const DEFAULT_RULES = [
  ["Stay on scheduling", "Stay focused on appointment booking, rescheduling, cancellation, and clinic scheduling questions."],
  ["No medical advice", "Do not provide medical advice or diagnose symptoms; recommend contacting a healthcare professional when needed."],
  ["Protect privacy", "Protect patient privacy and verify identity before sharing patient information."],
  ["Use real availability", "Only offer appointment times returned by the clinic system."],
  ["Escalate when needed", "Escalate urgent, unsafe, or out-of-scope requests to a human."],
];

export default function Guardrails() {
  const [guardrails, setGuardrails] = useState([]);
  const [error, setError] = useState("");

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

  return (
    <main className="clinic-page max-w-4xl">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-700">Configuration</p>
          <h2 className="text-3xl font-semibold tracking-[-0.04em] text-slate-950">Rules</h2>
          <p className="mt-2 text-sm text-slate-500">Code-enforced rules the virtual receptionist follows on every call.</p>
        </div>
        <span className="rounded-full bg-emerald-100 px-3 py-1.5 text-xs font-semibold text-emerald-800">
          Enforced
        </span>
      </div>
      <div className="space-y-3">
        {guardrails.map((rule, index) => (
          <div key={index} className="clinic-panel flex items-start gap-3 p-4 sm:p-5">
            <span className="mt-3 w-6 text-center font-mono text-xs tabular-nums text-slate-400">{String(index + 1).padStart(2, "0")}</span>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <p className="font-semibold text-slate-900">{rule.title}</p>
              <p className="text-sm leading-6 text-slate-500">{rule.description}</p>
            </div>
          </div>
        ))}
      </div>
      {error && <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p>}
    </main>
  );
}
