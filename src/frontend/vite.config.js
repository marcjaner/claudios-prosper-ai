import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const backendUrl = `http://127.0.0.1:${process.env.APP_BACKEND_PORT ?? 7860}`;

// Built assets are served from / by FastAPI; the dev server proxies the API
// so the same same-origin URLs work in both.
export default defineConfig({
  base: "/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: backendUrl, ws: true },
      "/ws": { target: backendUrl, ws: true },
    },
  },
});
