export default function Sparkline({ history }) {
  const peak = Math.max(1, ...history);
  return (
    <div className="flex h-8 items-end gap-px">
      {history.map((value, index) => (
        <div
          key={index}
          // Idle seconds keep a visible baseline, so a burst reads as a jump.
          style={{ height: `${Math.max(8, (value / peak) * 100)}%` }}
          className={`w-1 rounded-sm ${value > 0 ? "bg-emerald-500/70" : "bg-slate-700/60"}`}
        />
      ))}
    </div>
  );
}
