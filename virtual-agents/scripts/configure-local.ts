import { chmodSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { readEnvironment } from "../src/config.js";
import { AppError } from "../src/errors.js";

try {
  const { values } = parseArgs({ options: {
    endpoint: { type: "string" }, "record-audio": { type: "boolean" }, "output-gain-db": { type: "string" },
    "voice-provider": { type: "string" }, "voice-connector": { type: "string" }, "live-deployment": { type: "string" },
    "live-backend": { type: "string" }, "live-output-gain-db": { type: "string" },
    "openai-model": { type: "string" },
  } });
  const outputGain = values["output-gain-db"] === undefined ? undefined : Number(values["output-gain-db"]);
  if (outputGain !== undefined && (!Number.isFinite(outputGain) || outputGain < 0 || outputGain > 12)) {
    throw new AppError("invalid_output_gain", "Output gain must be between 0 and 12 dB.");
  }
  const connector = values["voice-connector"];
  if (connector !== undefined && connector !== "realtime" && connector !== "live") {
    throw new AppError("invalid_voice_connector", "Choose realtime or live; no configuration was changed.");
  }
  const environment = readEnvironment();
  const provider = values["voice-provider"] ?? environment.VOICE_PROVIDER ?? "azure";
  if (provider !== "azure" && provider !== "openai") {
    throw new AppError("invalid_voice_provider", "Choose azure or openai; no configuration was changed.");
  }
  if (provider === "openai" && (connector ?? environment.VOICE_CONNECTOR ?? "realtime") === "live") {
    throw new AppError("invalid_voice_connector", "OpenAI supports the realtime connector; no configuration was changed.");
  }
  const liveGain = values["live-output-gain-db"] === undefined ? undefined : Number(values["live-output-gain-db"]);
  if (liveGain !== undefined && (!Number.isFinite(liveGain) || liveGain < 0 || liveGain > 12)) {
    throw new AppError("invalid_output_gain", "Output gain must be between 0 and 12 dB.");
  }
  for (const value of [values["live-deployment"], values["live-backend"], values["openai-model"]]) {
    if (value !== undefined && (!value.trim() || /[\r\n]/.test(value))) throw new AppError("invalid_deployment");
  }
  const endpoint = values.endpoint ?? environment.AZURE_OPENAI_ENDPOINT;
  if (!endpoint && provider === "azure") {
    throw new AppError("missing_endpoint", "Use --endpoint https://your-resource.openai.azure.com or configure .env.lang.");
  }
  let endpointOrigin: string | undefined;
  if (endpoint) {
    const url = new URL(endpoint);
    if (url.protocol !== "https:" || !url.hostname.endsWith(".openai.azure.com") ||
        url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new AppError("invalid_endpoint");
    }
    endpointOrigin = url.origin;
  }
  let contents = existsSync(".env.local") ? readFileSync(".env.local", "utf8") : "";
  const updated: string[] = [];
  const set = (name: string, value: string) => {
    const pattern = new RegExp(`^(?:export\\s+)?${name}\\s*=.*$`, "m");
    const line = `${name}=${JSON.stringify(value)}`;
    contents = pattern.test(contents)
      ? contents.replace(pattern, () => line)
      : `${contents}${contents.endsWith("\n") || !contents ? "" : "\n"}${line}\n`;
    updated.push(name);
  };
  if (endpointOrigin && (values.endpoint || !environment.AZURE_OPENAI_ENDPOINT)) {
    set("AZURE_OPENAI_ENDPOINT", endpointOrigin);
  }
  if (values["record-audio"]) {
    set("CALL_RECORDING_ENABLED", "true");
    set("CALL_AUDIO_RECORDING_ENABLED", "true");
  }
  if (outputGain !== undefined) set("VOICE_OUTPUT_GAIN_DB", String(outputGain));
  if (values["voice-provider"] !== undefined) set("VOICE_PROVIDER", provider);
  if (connector !== undefined) set("VOICE_CONNECTOR", connector);
  if (values["openai-model"] !== undefined) set("OPENAI_REALTIME_MODEL", values["openai-model"].trim());
  if (values["live-deployment"] !== undefined) set("AZURE_OPENAI_LIVE_DEPLOYMENT", values["live-deployment"].trim());
  if (values["live-backend"] !== undefined) set("AZURE_OPENAI_LIVE_BACKEND_DEPLOYMENT", values["live-backend"].trim());
  if (liveGain !== undefined) set("VOICE_LIVE_OUTPUT_GAIN_DB", String(liveGain));
  writeFileSync(".env.local", contents, { mode: 0o600 });
  chmodSync(".env.local", 0o600);
  if (existsSync(".env.lang")) chmodSync(".env.lang", 0o600);
  console.log(JSON.stringify({ configured: true, updated, values: "not displayed" }));
} catch (error) {
  console.error(error instanceof AppError ? error.message : "Configuration failed; values are not displayed.");
  process.exitCode = 1;
}
