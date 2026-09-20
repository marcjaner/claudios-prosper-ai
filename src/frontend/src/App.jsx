import { useEffect, useState } from "react";

import Builder from "./Builder.jsx";
import CallDetail from "./CallDetail.jsx";
import History from "./History.jsx";
import Wall from "./Wall.jsx";
import Guardrails from "./Guardrails.jsx";
import { useLiveCalls } from "./useLiveCalls.js";

const VIEWS = {
  "#/wall": { label: "Monitor", component: Wall },
  "#/historico": { label: "History", component: History },
  "#/guardrails": { label: "Rules", component: Guardrails },
  "#/builder": { label: "Agent", component: Builder },
};
const ROUTES = VIEWS;
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

  const live = [...calls.values()].filter((call) => !call.ended_at);

  const callMatch = route.match(CALL_ROUTE);
  const openCall = callMatch?.[1];
  const returnTo = callMatch?.[2] === "wall" ? "#/wall" : "#/historico";
  const view = ROUTES[route] ? route : DEFAULT_VIEW;
  const View = ROUTES[view].component;
  const activeView = openCall ? returnTo : view;

  return (
    <div className={`clinic-theme min-h-screen bg-[#eef3f1] text-slate-900 ${openCall ? "xl:flex xl:h-dvh xl:min-h-0 xl:flex-col xl:overflow-hidden" : ""}`}>
      <header className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-3 border-b border-slate-200/80 bg-white/90 px-5 py-4 sm:grid-cols-[1fr_auto_1fr] sm:px-8">
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
        <nav className="order-3 col-span-2 flex justify-self-center gap-1 rounded-xl border border-slate-200 bg-slate-50 p-1 sm:order-none sm:col-span-1">
          {Object.entries(VIEWS).map(([hash, { label }]) => (
            <a
              key={hash}
              href={hash}
              className={`rounded-lg px-3.5 py-2 text-sm font-medium transition-colors ${
                activeView === hash
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-400 hover:bg-white hover:text-slate-700"
              }`}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="flex items-center justify-self-end gap-3">
          <div className="text-right">
            <p className="text-xl font-semibold tabular-nums text-emerald-600">{live.length}</p>
            <p className="text-xs text-slate-400">active</p>
          </div>
          <span
            className={`rounded-full px-3 py-1.5 text-xs font-medium ${
              connected ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"
            }`}
          >
            {connected ? "connected" : "reconnecting…"}
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
