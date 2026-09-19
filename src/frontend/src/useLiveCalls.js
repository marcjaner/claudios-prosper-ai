import { useEffect, useRef, useState } from "react";

const RECONNECT_MS = 1000;
// A three-minute call cannot produce more than this; the cap only guards
// against a stuck pipeline filling a browser tab.
const MAX_EVENTS_PER_CALL = 1000;

function liveUrl() {
  const url = new URL("/api/live", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/** Calls keyed by call_id, seeded by the snapshot and patched by the stream. */
export function useLiveCalls() {
  const [calls, setCalls] = useState(new Map());
  const [events, setEvents] = useState(new Map());
  const [connected, setConnected] = useState(false);
  // Partial transcripts arrive many times a second; batching into one frame
  // keeps twenty cards from re-rendering per word.
  const pending = useRef([]);
  const frame = useRef(null);

  useEffect(() => {
    let socket;
    let retry;
    let closed = false;

    const flush = () => {
      frame.current = null;
      const batch = pending.current;
      pending.current = [];
      if (batch.length === 0) return;
      const patches = batch.filter((item) => item.type === "call");
      if (patches.length > 0) {
        setCalls((previous) => {
          const next = new Map(previous);
          for (const { call_id, fields } of patches) {
            next.set(call_id, { ...(next.get(call_id) ?? { call_id }), ...fields });
          }
          return next;
        });
      }

      const arrivals = batch.filter((item) => item.type === "event");
      if (arrivals.length > 0) {
        setEvents((previous) => {
          const next = new Map(previous);
          for (const item of arrivals) {
            const existing = next.get(item.call_id) ?? [];
            next.set(
              item.call_id,
              [...existing, item].slice(-MAX_EVENTS_PER_CALL),
            );
          }
          return next;
        });
      }
    };

    const schedule = (patch) => {
      pending.current.push(patch);
      frame.current ??= requestAnimationFrame(flush);
    };

    const connect = () => {
      socket = new WebSocket(liveUrl());

      socket.onopen = () => setConnected(true);

      socket.onmessage = (message) => {
        const data = JSON.parse(message.data);
        if (data.type === "snapshot") {
          setCalls(new Map(data.calls.map((call) => [call.call_id, call])));
        } else if (data.type === "call" || data.type === "event") {
          schedule(data);
        }
      };

      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, RECONNECT_MS);
      };
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      if (frame.current) cancelAnimationFrame(frame.current);
      socket?.close();
    };
  }, []);

  return { calls, events, connected };
}
