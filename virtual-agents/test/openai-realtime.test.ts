import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { setImmediate as settle } from "node:timers/promises";
import { test } from "node:test";
import { ROOT_CONTEXT } from "@opentelemetry/api";
import WebSocket from "ws";
import { z } from "zod";
import type { AudioChunk } from "../src/audio.js";
import type { CallRecordEvent } from "../src/call-records.js";
import { parseConfig } from "../src/config.js";
import { createOpenAIVoiceFactory, openAIRealtimeUrl } from "../src/openai-realtime.js";
import { ProsperClient } from "../src/prosper.js";
import { receptionistTools } from "../src/receptionist.js";

const eventSchema = z.object({ type: z.string() }).passthrough();

class FakeSocket extends EventEmitter {
  readyState: number = WebSocket.CONNECTING;
  bufferedAmount = 0;
  readonly sent: z.infer<typeof eventSchema>[] = [];

  open(): void {
    this.readyState = WebSocket.OPEN;
    this.emit("open");
  }

  receive(event: Record<string, unknown>): void {
    this.emit("message", Buffer.from(JSON.stringify(event)), false);
  }

  send(payload: string): void {
    this.sent.push(eventSchema.parse(JSON.parse(payload)));
  }

  close(): void {
    this.readyState = WebSocket.CLOSED;
    this.emit("close");
  }

  terminate(): void {
    this.close();
  }
}

test("OpenAI Realtime uses bearer auth, GA PCMU configuration and normalized output events", async (t) => {
  const config = parseConfig({
    VOICE_PROVIDER: "openai",
    OPENAI_API_KEY: "offline-openai-key",
    OPENAI_REALTIME_MODEL: "gpt-realtime-mini",
    OPENAI_REALTIME_VOICE: "coral",
    OPENAI_REALTIME_TRANSCRIPTION_MODEL: "whisper-1",
    PROSPER_API_BASE_URL: "https://clinic.example",
    PROSPER_API_KEY: "offline-prosper-key",
  });
  const socket = new FakeSocket();
  const connections: { url: URL; options: WebSocket.ClientOptions }[] = [];
  const audio: AudioChunk[] = [];
  const finished: string[] = [];
  const records: CallRecordEvent[] = [];
  const failures: Error[] = [];
  let turns = 0;
  const controller = new AbortController();
  const factory = createOpenAIVoiceFactory(config, new ProsperClient(config), {
    createWebSocket: (url, options) => {
      connections.push({ url: new URL(url), options });
      return socket as unknown as WebSocket;
    },
  });
  const connecting = factory({
    callId: "openai-offline-call",
    parent: ROOT_CONTEXT,
    signal: controller.signal,
    greet: false,
    onAudio: (chunk) => audio.push(chunk),
    onAudioDone: (itemId) => finished.push(itemId),
    onInterrupt: () => undefined,
    onFailure: (error) => failures.push(error),
    onTurnDone: () => { turns += 1; },
    onRecord: (event) => records.push(event),
  });
  void connecting.catch(() => {});
  t.after(async () => {
    controller.abort();
    await (await connecting.catch(() => undefined))?.close();
  });

  await settle();
  socket.open();
  const update = z.object({ session: z.record(z.string(), z.unknown()) }).parse(socket.sent[0]).session;
  assert.deepEqual(connections[0]?.options.headers, { Authorization: "Bearer offline-openai-key" });
  assert.equal(connections[0]?.url.toString(), openAIRealtimeUrl(config));
  assert.equal(connections[0]?.url.pathname, "/v1/realtime");
  assert.equal(connections[0]?.url.searchParams.get("model"), "gpt-realtime-mini");
  assert.ok(!connections[0]?.url.toString().includes("offline-openai-key"));
  assert.equal(update.type, "realtime");
  assert.equal(update.model, "gpt-realtime-mini");
  assert.deepEqual(update.output_modalities, ["audio"]);
  assert.deepEqual(update.audio, {
    input: {
      format: { type: "audio/pcmu" },
      transcription: { model: "whisper-1" },
      turn_detection: { type: "semantic_vad", eagerness: "auto" },
    },
    output: { format: { type: "audio/pcmu" }, voice: "coral" },
  });
  assert.deepEqual(update.tools, receptionistTools);
  assert.equal(update.max_output_tokens, 512);
  assert.deepEqual(update.truncation, {
    type: "retention_ratio",
    retention_ratio: 0.8,
    token_limits: { post_instructions: 6000 },
  });

  socket.receive({ type: "session.updated" });
  const session = await connecting;
  session.sendText("Hello");
  socket.receive({ type: "response.created", response: { id: "response-1", status: "in_progress" } });
  socket.receive({
    type: "response.output_audio.delta", response_id: "response-1", item_id: "item-1",
    content_index: 0, delta: Buffer.alloc(160, 0x80).toString("base64"),
  });
  socket.receive({
    type: "conversation.item.input_audio_transcription.completed",
    item_id: "caller-1", transcript: "Hello",
  });
  socket.receive({
    type: "response.output_audio_transcript.done",
    response_id: "response-1", item_id: "item-1", transcript: "How can I help?",
  });
  socket.receive({
    type: "response.output_audio.done", response_id: "response-1", item_id: "item-1", content_index: 0,
  });
  socket.receive({
    type: "response.done",
    response: { id: "response-1", status: "completed", usage: { input_tokens: 2, output_tokens: 3 } },
  });
  socket.receive({
    type: "rate_limits.updated",
    rate_limits: [{ name: "tokens", limit: 200_000, remaining: 193_000, reset_seconds: 1.25 }],
  });
  await settle();

  assert.equal(audio.length, 1);
  assert.deepEqual(finished, ["item-1"]);
  assert.deepEqual(records.filter((event) => event.type === "transcript").map((event) => event.text), [
    "Hello", "Hello", "How can I help?",
  ]);
  assert.equal(turns, 1);
  assert.deepEqual(records.find((event) => event.type === "rate_limit"), {
    type: "rate_limit", name: "tokens", limit: 200_000, remaining: 193_000, resetSeconds: 1.25,
  });
  assert.deepEqual(failures, []);
});
