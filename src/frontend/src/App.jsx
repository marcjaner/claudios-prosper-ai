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
};
// Reachable by URL but deliberately out of the nav: the builder configures the
// agent, it is not part of the console a clinic — or the jury — is shown.
const TOOLS = {
  "#/builder": { label: "Agente", component: Builder },
};
const ROUTES = { ...VIEWS, ...TOOLS };
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

const CALL_ROUTE = /^#\/call\/([^?]+)(?:\?from=(wall|history))?$/;

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

  const callMatch = route.match(CALL_ROUTE);
  const openCall = callMatch?.[1];
  const returnTo = callMatch?.[2] === "wall" ? "#/wall" : "#/historico";
  const view = ROUTES[route] ? route : DEFAULT_VIEW;
  const View = ROUTES[view].component;
  const clinicTheme = Boolean(openCall) || view === "#/wall" || view === "#/historico";
  const activeView = openCall ? returnTo : view;

  return (
    <div className={`min-h-screen ${clinicTheme ? "clinic-theme bg-[#eef3f1] text-slate-900" : "bg-slate-950 text-slate-200"}`}>
      <header className={clinicTheme
        ? "grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-3 border-b border-slate-200/80 bg-white/90 px-5 py-4 sm:grid-cols-[1fr_auto_1fr] sm:px-8"
        : "flex items-center gap-6 border-b border-slate-800 px-6 py-4"
      }>
        {clinicTheme ? (
          <a href="#/wall" aria-label="Clinic home" className="group inline-flex w-fit items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-500 text-white shadow-[0_5px_16px_rgba(16,185,129,0.22)]">
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 fill-none stroke-current" strokeWidth="2.2" strokeLinecap="round">
                <path d="M7.5 5.5a8 8 0 1 0 0 13" />
                <path d="M16.5 7.5v6M13.5 10.5h6" />
              </svg>
            </span>
            <span className="text-[23px] font-semibold tracking-[-0.065em] text-slate-950">
              clini<span className="text-emerald-500">c</span>
            </span>
          </a>
        ) : (
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Clínica Arenal</h1>
            <p className="text-xs text-slate-500">consola de llamadas</p>
          </div>
        )}
        <nav className={clinicTheme
          ? "order-3 col-span-2 flex justify-self-center gap-1 rounded-xl border border-slate-200 bg-slate-50 p-1 sm:order-none sm:col-span-1"
          : "flex gap-1 rounded-lg bg-slate-900/70 p-1"
        }>
          {Object.entries(VIEWS).map(([hash, { label }]) => (
            <a
              key={hash}
              href={hash}
              className={clinicTheme
                ? `rounded-lg px-3.5 py-2 text-sm font-medium transition-colors ${
                    activeView === hash
                      ? "bg-white text-slate-900 shadow-sm"
                      : "text-slate-400 hover:bg-white hover:text-slate-700"
                  }`
                : `rounded-md px-3 py-1 text-sm transition-colors ${
                    !openCall && view === hash
                      ? "bg-slate-800 text-slate-100"
                      : "text-slate-500 hover:text-slate-300"
                  }`
              }
            >
              {clinicTheme
                ? { "#/wall": "Wall", "#/historico": "History" }[hash]
                : label}
            </a>
          ))}
        </nav>
        <div className={`flex items-center ${clinicTheme ? "justify-self-end gap-3" : "ml-auto gap-6"}`}>
          {!clinicTheme && <Sparkline history={history} />}
          <div className="text-right">
            <p className={`${clinicTheme ? "text-xl font-semibold text-emerald-600" : "font-mono text-3xl text-emerald-400"} tabular-nums`}>{live.length}</p>
            <p className={`text-xs ${clinicTheme ? "text-slate-400" : "uppercase tracking-wide text-slate-500"}`}>
              {clinicTheme ? "active" : "en curso"}
            </p>
          </div>
          <span
            className={`rounded-full px-3 py-1.5 text-xs font-medium ${
              connected
                ? clinicTheme ? "bg-emerald-50 text-emerald-700" : "bg-emerald-500/10 text-emerald-400"
                : clinicTheme ? "bg-rose-50 text-rose-700" : "bg-rose-500/10 text-rose-400"
            }`}
          >
            {connected
              ? clinicTheme ? "connected" : "conectado"
              : clinicTheme ? "reconnecting…" : "reconectando…"}
          </span>
        </div>
      </header>

      {openCall ? (
        <CallDetail
          callId={openCall}
          liveCall={calls.get(openCall)}
          liveEvents={events.get(openCall) ?? []}
          returnTo={returnTo}
        />
      ) : (
        <View calls={calls} events={events} />
      )}
    </div>
  );
}
