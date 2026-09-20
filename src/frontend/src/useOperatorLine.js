import { useCallback, useEffect, useRef, useState } from "react";

// Both directions cross the socket as PCM16 mono at this rate.
const SAMPLE_RATE = 16000;
const CAPTURE_BUFFER_SAMPLES = 2048;
const PLAYBACK_LEAD_SECONDS = 0.02;
// The server sends this as text once the agent has said its hand-off phrase.
const LINE_OPEN = "live";

function operatorUrl(callId) {
  const url = new URL(`/api/calls/${encodeURIComponent(callId)}/operator`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function toPcm16(samples) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    pcm[i] = Math.max(-32768, Math.min(32767, Math.round(samples[i] * 32767)));
  }
  return pcm.buffer;
}

/**
 * The operator's end of a taken-over call: microphone out, caller audio in.
 * Phases: idle → connecting → handoff (agent is saying goodbye) → live → ended.
 */
export function useOperatorLine(callId) {
  const [phase, setPhase] = useState("idle");
  const line = useRef(null);

  const hangUp = useCallback(() => {
    const current = line.current;
    line.current = null;
    if (!current) return;
    current.socket.onclose = null;
    current.socket.close();
    current.processor.disconnect();
    current.source.disconnect();
    current.stream.getTracks().forEach((track) => track.stop());
    current.context.close();
    setPhase("ended");
  }, []);

  const start = useCallback(async () => {
    if (line.current) return;
    setPhase("connecting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      const context = new AudioContext({ sampleRate: SAMPLE_RATE });
      await context.resume();
      const socket = new WebSocket(operatorUrl(callId));
      socket.binaryType = "arraybuffer";
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(CAPTURE_BUFFER_SAMPLES, 1, 1);
      let playAt = context.currentTime;
      line.current = { stream, context, socket, source, processor };

      const openMicrophone = () => {
        processor.onaudioprocess = (event) => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(toPcm16(event.inputBuffer.getChannelData(0)));
          }
        };
        source.connect(processor);
        processor.connect(context.destination);
        setPhase("live");
      };
      const play = (data) => {
        const pcm = new Int16Array(data);
        const buffer = context.createBuffer(1, pcm.length, SAMPLE_RATE);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < pcm.length; i += 1) channel[i] = pcm[i] / 32768;
        const player = context.createBufferSource();
        player.buffer = buffer;
        player.connect(context.destination);
        playAt = Math.max(playAt, context.currentTime + PLAYBACK_LEAD_SECONDS);
        player.start(playAt);
        playAt += buffer.duration;
      };

      socket.onopen = () => setPhase("handoff");
      socket.onmessage = (message) => {
        if (message.data === LINE_OPEN) openMicrophone();
        else if (message.data instanceof ArrayBuffer) play(message.data);
      };
      socket.onclose = (event) => {
        // The server closes the socket when the call ends or is already held.
        hangUp();
        if (event.code >= 4000) setPhase("error");
      };
      socket.onerror = () => setPhase("error");
    } catch {
      hangUp();
      setPhase("error");
    }
  }, [callId, hangUp]);

  useEffect(() => hangUp, [hangUp]);

  return { phase, start, hangUp };
}
