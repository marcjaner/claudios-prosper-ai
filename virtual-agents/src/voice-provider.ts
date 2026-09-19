import { createAzureVoiceFactory, type VoiceFactory } from "./azure-realtime.js";
import { createAzureLiveVoiceFactory } from "./azure-live.js";
import type { Config } from "./config.js";
import { createOpenAIVoiceFactory } from "./openai-realtime.js";
import type { ProsperClient } from "./prosper.js";

export function voiceProfile(config: Config) {
  return config.VOICE_CONNECTOR === "live"
    ? {
      provider: "azure" as const,
      connector: "live" as const,
      deployment: config.AZURE_OPENAI_LIVE_DEPLOYMENT,
      backendDeployment: config.AZURE_OPENAI_LIVE_BACKEND_DEPLOYMENT,
      outputGainDb: config.VOICE_LIVE_OUTPUT_GAIN_DB,
    }
    : {
      provider: config.VOICE_PROVIDER,
      connector: "realtime" as const,
      deployment: config.VOICE_PROVIDER === "openai"
        ? config.OPENAI_REALTIME_MODEL : config.AZURE_OPENAI_DEPLOYMENT,
      outputGainDb: config.VOICE_OUTPUT_GAIN_DB,
    };
}

export function createConfiguredVoiceFactory(
  config: Config,
  prosper: ProsperClient,
  factories: {
    azureRealtime?: typeof createAzureVoiceFactory;
    openAIRealtime?: typeof createOpenAIVoiceFactory;
    live?: typeof createAzureLiveVoiceFactory;
  } = {},
): VoiceFactory {
  if (config.VOICE_CONNECTOR === "live") {
    return (factories.live ?? createAzureLiveVoiceFactory)(config, prosper);
  }
  return config.VOICE_PROVIDER === "openai"
    ? (factories.openAIRealtime ?? createOpenAIVoiceFactory)(config, prosper)
    : (factories.azureRealtime ?? createAzureVoiceFactory)(config, prosper);
}
