export function SparklesIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-3.5 w-3.5 fill-none stroke-current"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3l1.1 3.4L16.5 7.5l-3.4 1.1L12 12l-1.1-3.4-3.4-1.1 3.4-1.1L12 3zM18 13l.8 2.2L21 16l-2.2.8L18 19l-.8-2.2L15 16l2.2-.8L18 13zM6 14l.7 2.3L9 17l-2.3.7L6 20l-.7-2.3L3 17l2.3-.7L6 14z" />
    </svg>
  );
}

export function PromptActions({ busy, onImprove, onValidate, validateLabel = "Validar etapa" }) {
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      <button
        type="button"
        onClick={onImprove}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-slate-700 disabled:cursor-wait disabled:opacity-50"
      >
        <SparklesIcon />
        Mejorar con IA
      </button>
      <button
        type="button"
        onClick={onValidate}
        disabled={busy}
        className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 transition-colors hover:border-emerald-300 hover:text-emerald-700 disabled:cursor-wait disabled:opacity-50"
      >
        {validateLabel}
      </button>
    </div>
  );
}

export function RevisionPanel({ revision, onApply, onDiscard }) {
  return (
    <section className="mt-3 overflow-hidden rounded-xl border border-emerald-200 bg-emerald-50/60">
      <div className="flex items-center justify-between border-b border-emerald-100 px-3 py-2.5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700">Propuesta de IA</p>
          <p className="mt-0.5 text-[11px] text-emerald-700/70">No se aplicará hasta que la aceptes.</p>
        </div>
        <button type="button" onClick={onDiscard} aria-label="Descartar propuesta" className="rounded-md p-1 text-emerald-700/50 hover:bg-white hover:text-emerald-800">×</button>
      </div>
      <div className="space-y-3 p-3">
        {revision.changes.length > 0 && (
          <ul className="space-y-1 text-xs text-slate-600">
            {revision.changes.map((change) => <li key={change}>• {change}</li>)}
          </ul>
        )}
        {revision.concerns.length > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs text-amber-800">
            {revision.concerns.map((concern) => <p key={concern}>{concern}</p>)}
          </div>
        )}
        <div className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg border border-emerald-100 bg-white px-3 py-2.5 text-xs leading-5 text-slate-700">
          {revision.revised_prompt}
        </div>
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer font-medium">Ver texto original</summary>
          <div className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-white/70 px-3 py-2 leading-5">{revision.original}</div>
        </details>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onDiscard} className="rounded-lg px-3 py-2 text-xs font-semibold text-slate-500 hover:bg-white">Descartar</button>
          <button type="button" onClick={onApply} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-700">Aplicar propuesta</button>
        </div>
      </div>
    </section>
  );
}

const FINDING_STYLE = {
  error: { label: "Error", dot: "bg-rose-500", badge: "bg-rose-50 text-rose-700" },
  warning: { label: "Aviso", dot: "bg-amber-400", badge: "bg-amber-50 text-amber-700" },
  suggestion: { label: "Sugerencia", dot: "bg-sky-400", badge: "bg-sky-50 text-sky-700" },
};

export function ValidationPanel({ report, isStale, onClose, onNavigate }) {
  const counts = report.findings.reduce((result, finding) => {
    result[finding.severity] += 1;
    return result;
  }, { error: 0, warning: 0, suggestion: 0 });

  return (
    <section className="mb-5 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50/80 shadow-sm">
      <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-3.5 py-3">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-slate-900">Validación</p>
            {isStale && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">Desactualizada</span>}
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            {report.findings.length === 0
              ? "No se han encontrado problemas."
              : `${counts.error} errores · ${counts.warning} avisos · ${counts.suggestion} sugerencias`}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Cerrar validación" className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
      </div>
      {report.findings.length === 0 ? (
        <div className="px-4 py-5 text-center text-sm text-emerald-700">La configuración es coherente.</div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto p-2.5">
          {report.findings.map((finding, index) => {
            const style = FINDING_STYLE[finding.severity];
            return (
              <article key={`${finding.title}-${index}`} className="rounded-xl border border-slate-200 bg-white p-3">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${style.badge}`}>{style.label}</span>
                  <h3 className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-800">{finding.title}</h3>
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-600">{finding.message}</p>
                {finding.suggestion && <p className="mt-1.5 text-xs leading-5 text-slate-400">{finding.suggestion}</p>}
                {finding.sources.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {finding.sources.map((source) => (
                      <button
                        type="button"
                        key={`${source.kind}-${source.id}`}
                        onClick={() => onNavigate(source)}
                        className="rounded-md bg-slate-100 px-2 py-1 font-mono text-[10px] text-slate-500 hover:bg-emerald-50 hover:text-emerald-700"
                      >
                        {source.kind === "system" ? "generales" : source.id.replace("->", " → ")}
                      </button>
                    ))}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
