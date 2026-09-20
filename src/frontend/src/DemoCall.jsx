import { useEffect, useState } from "react";

import { useDemoCall } from "./useDemoCall.js";

const DEFAULT_CALLER_NUMBER = "+34612345678";
const WAVE_BARS = [18, 30, 44, 58, 38, 52, 26, 42, 20];

function PhoneIcon({ className = "h-5 w-5" }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className={`${className} fill-none stroke-current`} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.78 19.78 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.78 19.78 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.12.9.33 1.78.62 2.63a2 2 0 0 1-.45 2.11L8 9.73a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.85.29 1.73.5 2.63.62A2 2 0 0 1 22 16.92Z" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 fill-none stroke-current" strokeWidth="2" strokeLinecap="round">
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

function MinimizeIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 fill-none stroke-current" strokeWidth="2" strokeLinecap="round">
      <path d="M6 12h12" />
    </svg>
  );
}

function MicrophoneIcon({ muted = false }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 fill-none stroke-current" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8" />
      {muted ? <path d="m3 3 18 18" /> : null}
    </svg>
  );
}

function formatDuration(startedAt, endedAt, now) {
  if (!startedAt) return "0:00";
  const seconds = Math.max(0, Math.floor(((endedAt ?? now) - startedAt) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function useCallDuration(startedAt, endedAt) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!startedAt || endedAt) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [endedAt, startedAt]);
  return formatDuration(startedAt, endedAt, now);
}

function AudioWave({ active }) {
  return (
    <div className="flex h-20 items-center justify-center gap-1.5" aria-hidden="true">
      {WAVE_BARS.map((height, index) => (
        <span
          key={`${height}-${index}`}
          className={`call-wave-bar w-1.5 rounded-full ${active ? "bg-emerald-400" : "bg-slate-200"}`}
          style={{ height, animationDelay: `${index * -110}ms` }}
        />
      ))}
    </div>
  );
}

function AgentPulse({ active }) {
  return (
    <span className="agent-pulse" aria-hidden="true">
      {[10, 18, 25, 16, 22].map((height, index) => (
        <span
          key={`${height}-${index}`}
          className="agent-pulse-bar"
          style={{ height, animationDelay: `${index * -120}ms`, animationPlayState: active ? "running" : "paused" }}
        />
      ))}
    </span>
  );
}

function SetupCall({ callerNumber, setCallerNumber, withholdCallerId, setWithholdCallerId, startCall }) {
  return (
    <div className="demo-drawer-enter flex flex-1 flex-col px-6 pb-7">
      <div className="demo-call-intro relative mb-7 overflow-hidden rounded-[22px] border border-emerald-100 bg-emerald-50/70 p-5">
        <div className="relative z-10 flex items-center gap-4">
          <div className="demo-phone-icon relative flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-emerald-500 text-white shadow-[0_10px_24px_rgba(16,185,129,0.25)]">
            <span className="call-signal-ring absolute inset-0 rounded-2xl border border-emerald-400" />
            <span className="call-signal-ring absolute inset-0 rounded-2xl border border-emerald-400 [animation-delay:1.1s]" />
            <PhoneIcon />
          </div>
          <h3 className="text-lg font-semibold tracking-tight text-slate-950">Call the clinic agent</h3>
        </div>
        <p className="relative z-10 mt-4 text-sm leading-6 text-slate-500">
          Use your microphone to test a real appointment conversation from this dashboard.
        </p>
      </div>

      <label htmlFor="demo-caller-number" className="text-sm font-semibold text-slate-700">
        Caller number
      </label>
      <input
        id="demo-caller-number"
        type="tel"
        value={callerNumber}
        onChange={(event) => setCallerNumber(event.target.value)}
        disabled={withholdCallerId}
        className="clinic-control mt-2 disabled:bg-slate-50 disabled:text-slate-400"
        autoComplete="tel"
      />
      <label className="mt-4 flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-slate-200 px-4 py-3.5">
        <span>
          <span className="block text-sm font-semibold text-slate-700">Private number</span>
          <span className="mt-0.5 block text-xs text-slate-400">Hide caller ID from the agent</span>
        </span>
        <input
          type="checkbox"
          checked={withholdCallerId}
          onChange={(event) => setWithholdCallerId(event.target.checked)}
          className="h-4 w-4 accent-emerald-500"
        />
      </label>

      <button
        type="button"
        onClick={() => startCall(callerNumber, withholdCallerId)}
        className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 py-3.5 text-sm font-semibold text-white shadow-lg shadow-slate-950/10 transition hover:-translate-y-0.5 hover:bg-emerald-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
      >
        <PhoneIcon className="h-4 w-4" />
        Call Agent
      </button>
      <p className="mt-auto pt-6 text-center text-xs leading-5 text-slate-400">
        Headphones recommended to prevent speaker feedback.
      </p>
    </div>
  );
}

function ActiveCall({ callerNumber, withholdCallerId, status, duration, isMuted, toggleMute, finishCall }) {
  const isConnecting = status === "connecting";
  return (
    <div className="flex flex-1 flex-col px-6 pb-7">
      <div className="relative overflow-hidden rounded-[26px] bg-slate-950 px-6 py-7 text-center text-white shadow-[0_20px_55px_rgba(15,23,42,0.18)]">
        <div className="call-orbit absolute -right-14 -top-14 h-40 w-40 rounded-full border border-emerald-400/20" />
        <div className="call-orbit absolute -left-20 bottom-[-6rem] h-48 w-48 rounded-full border border-emerald-400/10 [animation-direction:reverse]" />
        <div className="relative">
          <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-300">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            {isConnecting ? "Connecting" : "Live call"}
          </span>
          <AudioWave active={!isConnecting && !isMuted} />
          <p className="font-mono text-4xl font-medium tracking-tight tabular-nums">{duration}</p>
          <p className="mt-2 text-sm text-slate-400">
            {isConnecting ? "Opening the agent line…" : isMuted ? "Your microphone is muted" : "Speak naturally — the agent is listening"}
          </p>
        </div>
      </div>

      <div className="mt-5 flex items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3.5">
        <div className="grid h-9 w-9 place-items-center rounded-full bg-white text-slate-500 shadow-sm">
          <PhoneIcon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="text-xs text-slate-400">Calling as</p>
          <p className="truncate text-sm font-semibold text-slate-800">
            {withholdCallerId ? "Private number" : callerNumber}
          </p>
        </div>
      </div>

      <div className="mt-auto grid grid-cols-2 gap-3 pt-7">
        <button
          type="button"
          onClick={toggleMute}
          disabled={isConnecting}
          className={`inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-3 text-sm font-semibold transition-colors disabled:opacity-40 ${
            isMuted
              ? "border-amber-200 bg-amber-50 text-amber-700"
              : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
          }`}
        >
          <MicrophoneIcon muted={isMuted} />
          {isMuted ? "Unmute" : "Mute"}
        </button>
        <button
          type="button"
          onClick={() => finishCall(true)}
          disabled={isConnecting}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-rose-500 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-rose-500/15 transition-colors hover:bg-rose-600 disabled:cursor-wait disabled:bg-rose-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600"
        >
          <PhoneIcon className="h-4 w-4 rotate-[135deg]" />
          End call
        </button>
      </div>
    </div>
  );
}

function FinishedCall({ callId, error, reset, closeDrawer }) {
  const failed = Boolean(error);
  return (
    <div className="flex flex-1 flex-col px-6 pb-7">
      <div className={`rounded-[22px] border p-5 ${failed ? "border-rose-100 bg-rose-50" : "border-emerald-100 bg-emerald-50"}`}>
        <span className={`grid h-11 w-11 place-items-center rounded-2xl text-xl font-semibold text-white ${failed ? "bg-rose-500" : "bg-emerald-500"}`}>
          {failed ? "!" : "✓"}
        </span>
        <h3 className="mt-5 text-lg font-semibold tracking-tight text-slate-950">
          {failed ? "Call interrupted" : "Demo call complete"}
        </h3>
        <p className="mt-1.5 text-sm leading-6 text-slate-500">
          {error || "The conversation is now available in the live monitor and call history."}
        </p>
      </div>

      {callId ? (
        <div className="mt-5 rounded-xl border border-slate-200 px-4 py-3">
          <p className="text-xs text-slate-400">Call ID</p>
          <p className="mt-1 truncate font-mono text-xs text-slate-600" title={callId}>{callId}</p>
        </div>
      ) : null}

      <div className="mt-auto space-y-3 pt-7">
        {!failed && callId ? (
          <a
            href={`#/call/${callId}?from=wall`}
            onClick={closeDrawer}
            className="flex w-full items-center justify-center rounded-xl bg-slate-950 px-4 py-3.5 text-sm font-semibold text-white transition-colors hover:bg-emerald-600"
          >
            View call details
          </a>
        ) : null}
        <button
          type="button"
          onClick={reset}
          className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50"
        >
          Start another call
        </button>
      </div>
    </div>
  );
}

export default function DemoCall() {
  const [isOpen, setIsOpen] = useState(false);
  const [callerNumber, setCallerNumber] = useState(DEFAULT_CALLER_NUMBER);
  const [withholdCallerId, setWithholdCallerId] = useState(false);
  const call = useDemoCall();
  const duration = useCallDuration(call.startedAt, call.endedAt);
  const isInCall = call.status === "connecting" || call.status === "active";

  return (
    <>
      {!isOpen && !isInCall ? (
        <button
          type="button"
          aria-expanded="false"
          aria-controls="demo-call-drawer"
          onClick={() => setIsOpen(true)}
          className="call-launcher-enter fixed bottom-6 right-6 z-40 inline-flex items-center gap-2.5 rounded-full border border-slate-200 bg-white px-5 py-3.5 text-sm font-semibold text-slate-800 shadow-[0_16px_42px_rgba(15,23,42,0.16)] transition hover:-translate-y-0.5 hover:border-emerald-300 hover:text-emerald-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
        >
          <span className="grid h-7 w-7 place-items-center rounded-full bg-emerald-500 text-white">
            <PhoneIcon className="h-3.5 w-3.5" />
          </span>
          Call Agent
        </button>
      ) : null}

      {!isOpen && isInCall ? (
        <div
          className="call-launcher-enter fixed bottom-6 right-6 z-40 flex items-center rounded-full border border-slate-700/80 bg-slate-950 p-1.5 text-white shadow-[0_18px_46px_rgba(15,23,42,0.3)]"
          aria-label={`Agent call in progress, ${duration}`}
        >
          <button
            type="button"
            onClick={() => setIsOpen(true)}
            aria-label={`Open active call, ${duration}`}
            aria-controls="demo-call-drawer"
            className="grid h-11 w-14 place-items-center rounded-full text-emerald-300 transition hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400"
          >
            <AgentPulse active={call.status === "active" && !call.isMuted} />
          </button>
          <span className="mx-1 h-6 w-px bg-white/15" aria-hidden="true" />
          <button
            type="button"
            onClick={call.toggleMute}
            disabled={call.status === "connecting"}
            aria-label={call.isMuted ? "Unmute microphone" : "Mute microphone"}
            aria-pressed={call.isMuted}
            title={call.isMuted ? "Unmute" : "Mute"}
            className={`grid h-11 w-11 place-items-center rounded-full transition disabled:cursor-wait disabled:opacity-40 ${
              call.isMuted ? "bg-amber-400/15 text-amber-300" : "text-slate-300 hover:bg-white/10 hover:text-white"
            }`}
          >
            <MicrophoneIcon muted={call.isMuted} />
          </button>
          <button
            type="button"
            onClick={() => call.finishCall(true)}
            disabled={call.status === "connecting"}
            aria-label="End call"
            title="End call"
            className="grid h-11 w-11 place-items-center rounded-full text-rose-400 transition hover:bg-rose-500 hover:text-white disabled:cursor-wait disabled:opacity-40"
          >
            <PhoneIcon className="h-5 w-5 rotate-[135deg]" />
          </button>
        </div>
      ) : null}

      <aside
        id="demo-call-drawer"
        aria-label="Call agent"
        aria-hidden={!isOpen}
        className={`call-agent-drawer clinic-theme fixed inset-x-3 inset-y-3 z-50 flex flex-col overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.2)] sm:left-auto sm:w-full sm:max-w-[420px] ${
          isOpen
            ? "visible translate-x-0 translate-y-0 scale-100 opacity-100"
            : "invisible translate-x-8 translate-y-4 scale-[0.96] opacity-0"
        }`}
      >
        <header className="flex items-center justify-between px-6 py-5">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-emerald-600">Browser line</p>
            <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">Call agent</h2>
          </div>
          <button
            type="button"
            onClick={() => setIsOpen(false)}
            aria-label={isInCall ? "Minimize active call" : "Close call panel"}
            className="grid h-10 w-10 place-items-center rounded-full border border-slate-200 text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900"
          >
            {isInCall ? <MinimizeIcon /> : <CloseIcon />}
          </button>
        </header>

        {call.status === "idle" ? (
          <SetupCall
            callerNumber={callerNumber}
            setCallerNumber={setCallerNumber}
            withholdCallerId={withholdCallerId}
            setWithholdCallerId={setWithholdCallerId}
            startCall={call.startCall}
          />
        ) : null}
        {isInCall ? (
          <ActiveCall
            callerNumber={callerNumber}
            withholdCallerId={withholdCallerId}
            status={call.status}
            duration={duration}
            isMuted={call.isMuted}
            toggleMute={call.toggleMute}
            finishCall={call.finishCall}
          />
        ) : null}
        {call.status === "ended" || call.status === "error" ? (
          <FinishedCall
            callId={call.callId}
            error={call.error}
            reset={call.reset}
            closeDrawer={() => setIsOpen(false)}
          />
        ) : null}
      </aside>
    </>
  );
}
