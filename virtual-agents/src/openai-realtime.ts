import type { Config } from "./config.js";
import {
  createRealtimeVoiceFactory,
  type AzureRealtimeDependencies,
  type VoiceFactory,
} from "./azure-realtime.js";
import type { ProsperClient } from "./prosper.js";
import { receptionistTools } from "./receptionist.js";

export interface OpenAIRealtimeDependencies {
  createWebSocket?: AzureRealtimeDependencies["createWebSocket"];
}

const EVENT_TYPES: Readonly<Record<string, string>> = {
  "response.output_audio.delta": "response.audio.delta",
  "response.output_audio.done": "response.audio.done",
  "response.output_audio_transcript.delta": "response.audio_transcript.delta",
  "response.output_audio_transcript.done": "response.audio_transcript.done",
  "response.output_text.done": "response.text.done",
};

const MAX_OUTPUT_TOKENS = 512;
const MAX_CONVERSATION_TOKENS = 6_000;

export function openAIRealtimeUrl(config: Config): string {
  const url = new URL("wss://api.openai.com/v1/realtime");
  url.searchParams.set("model", config.OPENAI_REALTIME_MODEL);
  return url.toString();
}

export function createOpenAIVoiceFactory(
  config: Config,
  prosper: ProsperClient,
  dependencies: OpenAIRealtimeDependencies = {},
): VoiceFactory {
  const realtimeDependencies = dependencies.createWebSocket
    ? { createWebSocket: dependencies.createWebSocket } : {};
  return createRealtimeVoiceFactory(prosper, {
    id: "openai",
    system: "openai",
    model: config.OPENAI_REALTIME_MODEL,
    serverAddress: "api.openai.com",
    url: openAIRealtimeUrl(config),
    headers: () => ({ Authorization: `Bearer ${config.OPENAI_API_KEY!}` }),
    session: (instructions) => ({
      type: "realtime",
      model: config.OPENAI_REALTIME_MODEL,
      output_modalities: ["audio"],
      audio: {
        input: {
          format: { type: "audio/pcmu" },
          transcription: { model: config.OPENAI_REALTIME_TRANSCRIPTION_MODEL },
          turn_detection: { type: "semantic_vad", eagerness: "auto" },
        },
        output: {
          format: { type: "audio/pcmu" },
          voice: config.OPENAI_REALTIME_VOICE,
        },
      },
      instructions,
      tools: receptionistTools,
      tool_choice: "auto",
      max_output_tokens: MAX_OUTPUT_TOKENS,
      truncation: {
        type: "retention_ratio",
        retention_ratio: 0.8,
        token_limits: { post_instructions: MAX_CONVERSATION_TOKENS },
      },
    }),
    normalizeEventType: (type) => EVENT_TYPES[type] ?? type,
  }, realtimeDependencies);
}
