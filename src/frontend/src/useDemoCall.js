import { useCallback, useEffect, useRef, useState } from "react";

const WIRE_RATE = 8000;
const FRAME_BYTES = 160;

function createSid(prefix) {
  return `${prefix}${crypto.randomUUID().replaceAll("-", "")}`;
}

function socketUrl() {
  const url = new URL("/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function linearToMulaw(sample) {
  const pcm = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
  const sign = pcm < 0 ? 0x80 : 0;
  const magnitude = Math.min(Math.abs(pcm) + 132, 32635);
  let exponent = 7;
  for (let mask = 0x4000; exponent > 0 && !(magnitude & mask); mask >>= 1) {
    exponent -= 1;
  }
  const mantissa = (magnitude >> (exponent + 3)) & 0x0f;
  return ~(sign | (exponent << 4) | mantissa) & 0xff;
}

function mulawToLinear(byte) {
  const value = ~byte & 0xff;
  const magnitude = (((value & 0x0f) << 3) + 132) << ((value & 0x70) >> 4);
  return ((value & 0x80) ? 132 - magnitude : magnitude - 132) / 32768;
}

function send(session, message) {
  if (session.socket.readyState === WebSocket.OPEN) {
    session.socket.send(JSON.stringify(message));
  }
}

function startMessage(session, callerNumber, withholdCallerId) {
  const customParameters = { call_id: session.callId };
  if (!withholdCallerId) customParameters.from_number = callerNumber.trim();

  return {
    event: "start",
    sequenceNumber: "1",
    streamSid: session.streamSid,
    start: {
      streamSid: session.streamSid,
      accountSid: "AClocal00000000000000000000000000",
      callSid: session.callId,
      tracks: ["inbound"],
      customParameters,
      mediaFormat: {
        encoding: "audio/x-mulaw",
        sampleRate: WIRE_RATE,
        channels: 1,
      },
    },
  };
}

function sendAudio(session, samples) {
  if (session.socket.readyState !== WebSocket.OPEN) return;

  session.inputSamples.push(...samples);
  const step = session.audioContext.sampleRate / WIRE_RATE;
  const downsampled = [];

  while (session.inputPosition + step < session.inputSamples.length) {
    const before = Math.floor(session.inputPosition);
    const fraction = session.inputPosition - before;
    downsampled.push(
      session.inputSamples[before] * (1 - fraction)
        + session.inputSamples[before + 1] * fraction,
    );
    session.inputPosition += step;
  }

  const consumed = Math.floor(session.inputPosition);
  session.inputSamples = session.inputSamples.slice(consumed);
  session.inputPosition -= consumed;
  session.mediaSamples.push(...downsampled);

  while (session.mediaSamples.length >= FRAME_BYTES) {
    const frame = session.mediaSamples.splice(0, FRAME_BYTES);
    const bytes = new Uint8Array(frame.map(linearToMulaw));
    const payload = btoa(String.fromCharCode(...bytes));
    session.sequence += 1;
    send(session, {
      event: "media",
      sequenceNumber: String(session.sequence),
      streamSid: session.streamSid,
      media: {
        track: "inbound",
        chunk: String(session.sequence - 1),
        timestamp: String((session.sequence - 2) * 20),
        payload,
      },
    });
  }
}

function playAudio(session, payload) {
  if (!payload) return;

  const bytes = Uint8Array.from(atob(payload), (character) => character.charCodeAt(0));
  const buffer = session.audioContext.createBuffer(1, bytes.length, WIRE_RATE);
  const channel = buffer.getChannelData(0);
  bytes.forEach((byte, index) => {
    channel[index] = mulawToLinear(byte);
  });

  const player = session.audioContext.createBufferSource();
  player.buffer = buffer;
  player.connect(session.audioContext.destination);
  session.outputAt = Math.max(
    session.outputAt,
    session.audioContext.currentTime + 0.02,
  );
  player.start(session.outputAt);
  session.outputAt += buffer.duration;
  session.activeSources.add(player);
  player.onended = () => session.activeSources.delete(player);
}

function clearPlayback(session) {
  session.activeSources.forEach((player) => {
    try {
      player.stop();
    } catch {
      // A source can finish between iteration and stop.
    }
  });
  session.activeSources.clear();
  session.outputAt = session.audioContext.currentTime;
}

export function useDemoCall() {
  const sessionRef = useRef(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [callId, setCallId] = useState("");
  const [startedAt, setStartedAt] = useState(null);
  const [endedAt, setEndedAt] = useState(null);
  const [isMuted, setIsMuted] = useState(false);

  const finishCall = useCallback((sendStop = true, outcome = "ended", message = "") => {
    const session = sessionRef.current;
    if (!session) return;
    sessionRef.current = null;

    if (sendStop) {
      send(session, {
        event: "stop",
        sequenceNumber: String(session.sequence + 1),
        streamSid: session.streamSid,
        stop: { callSid: session.callId },
      });
    }

    session.socket.onclose = null;
    session.socket.onerror = null;
    session.socket.close();
    session.processor?.disconnect();
    session.source?.disconnect();
    session.stream.getTracks().forEach((track) => track.stop());
    clearPlayback(session);
    session.audioContext.close();

    setEndedAt(Date.now());
    setIsMuted(false);
    setError(message);
    setStatus(outcome);
  }, []);

  const startCall = useCallback(async (callerNumber, withholdCallerId) => {
    if (sessionRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Microphone access needs localhost or a secure HTTPS connection.");
      setStatus("error");
      return;
    }

    const nextCallId = createSid("CA");
    setCallId(nextCallId);
    setStartedAt(null);
    setEndedAt(null);
    setError("");
    setStatus("connecting");

    let stream;
    let audioContext;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
        video: false,
      });
      audioContext = new AudioContext();
      await audioContext.resume();

      const session = {
        callId: nextCallId,
        streamSid: createSid("MZ"),
        sequence: 1,
        socket: new WebSocket(socketUrl()),
        stream,
        audioContext,
        source: null,
        processor: null,
        inputSamples: [],
        inputPosition: 0,
        mediaSamples: [],
        outputAt: audioContext.currentTime,
        activeSources: new Set(),
        connected: false,
      };
      sessionRef.current = session;

      session.socket.onopen = () => {
        session.connected = true;
        send(session, { event: "connected", protocol: "Call", version: "1.0.0" });
        send(session, startMessage(session, callerNumber, withholdCallerId));
        session.source = audioContext.createMediaStreamSource(stream);
        session.processor = audioContext.createScriptProcessor(2048, 1, 1);
        session.processor.onaudioprocess = (event) => {
          sendAudio(session, event.inputBuffer.getChannelData(0));
        };
        session.source.connect(session.processor);
        session.processor.connect(audioContext.destination);
        setStartedAt(Date.now());
        setStatus("active");
      };

      session.socket.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.event === "media") playAudio(session, message.media?.payload);
        if (message.event === "clear") clearPlayback(session);
      };
      session.socket.onerror = () => {
        finishCall(false, "error", "The agent WebSocket could not connect.");
      };
      session.socket.onclose = () => {
        finishCall(
          false,
          session.connected ? "ended" : "error",
          session.connected ? "" : "The agent WebSocket closed before the call connected.",
        );
      };
    } catch (caught) {
      stream?.getTracks().forEach((track) => track.stop());
      audioContext?.close();
      setError(caught instanceof Error ? caught.message : "The call could not start.");
      setStatus("error");
    }
  }, [finishCall]);

  const toggleMute = useCallback(() => {
    setIsMuted((current) => {
      const next = !current;
      sessionRef.current?.stream.getAudioTracks().forEach((track) => {
        track.enabled = !next;
      });
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    setStatus("idle");
    setError("");
    setCallId("");
    setStartedAt(null);
    setEndedAt(null);
  }, []);

  useEffect(() => () => finishCall(true), [finishCall]);

  return {
    callId,
    endedAt,
    error,
    finishCall,
    isMuted,
    reset,
    startedAt,
    startCall,
    status,
    toggleMute,
  };
}
