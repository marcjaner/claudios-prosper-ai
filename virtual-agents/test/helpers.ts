import { parseConfig } from "../src/config.js";

export function config(overrides: NodeJS.ProcessEnv = {}) {
  return parseConfig({
    AZURE_OPENAI_ENDPOINT: "https://example.openai.azure.com",
    PROSPER_API_KEY: "test-prosper-key",
    PORT: "0",
    CALL_RECORDING_ENABLED: "false",
    ...overrides,
  });
}
