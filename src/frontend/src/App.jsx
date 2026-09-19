import { useEffect, useRef, useState } from "react";

import Builder from "./Builder.jsx";
import CallDetail from "./CallDetail.jsx";
import History from "./History.jsx";
import Sparkline from "./Sparkline.jsx";
import Wall from "./Wall.jsx";
import { useLiveCalls } from "./useLiveCalls.js";

const VIEWS = {
  "#/wall": { label: "Wall", component: Wall },
  "#/historico": { label: "Histórico", component: History },
  "#/builder": { label: "Agente", component: Builder },
};
const DEFAULT_VIEW = "#/wall";

function useHashRoute() {
  const [hash, setHash] = useState(() => window.location.hash || DEFAULT_VIEW);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || DEFAULT_VIEW);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

const CALL_ROUTE = /^#\/call\/(.+)$/;

export default function App() {
  // The live socket stays open on both views, so switching back to the wall
  // never costs a reconnect in front of an audience.
  const { calls, events, connected } = useLiveCalls();
  const route = useHashRoute();
  const [history, setHistory] = useState(() => Array(60).fill(0));

  const live = [...calls.values()].filter((call) => !call.ended_at);

  // Read through a ref: keying the interval on the count would restart it on
  // every change, and during a burst it would never survive long enough to fire.
  const liveCount = useRef(0);
  liveCount.current = live.length;
  useEffect(() => {
    const timer = setInterval(
      () => setHistory((previous) => [...previous.slice(1), liveCount.current]),
      1000,
    );
    return () => clearInterval(timer);
  }, []);

  const openCall = route.match(CALL_ROUTE)?.[1];
  const view = VIEWS[route] ? route : DEFAULT_VIEW;
  const View = VIEWS[view].component;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200">
      <header className="flex items-center gap-6 border-b border-slate-800 px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Clínica Arenal</h1>
          <p className="text-xs text-slate-500">consola de llamadas</p>
        </div>
        <nav className="flex gap-1 rounded-lg bg-slate-900/70 p-1">
          {Object.entries(VIEWS).map(([hash, { label }]) => (
            <a
              key={hash}
              href={hash}
              className={`rounded-md px-3 py-1 text-sm transition-colors ${
                !openCall && view === hash
                  ? "bg-slate-800 text-slate-100"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-6">
          <Sparkline history={history} />
          <div className="text-right">
            <p className="font-mono text-3xl tabular-nums text-emerald-400">{live.length}</p>
            <p className="text-xs uppercase tracking-wide text-slate-500">en curso</p>
          </div>
          <span
            className={`rounded-full px-3 py-1 text-xs ${
              connected ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-400"
            }`}
          >
            {connected ? "conectado" : "reconectando…"}
          </span>
        </div>
      </header>

      {openCall ? (
        <CallDetail
          callId={openCall}
          liveCall={calls.get(openCall)}
          liveEvents={events.get(openCall) ?? []}
        />
      ) : (
        <View calls={calls} />
      )}
    </div>
  );
}
