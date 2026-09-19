import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built assets are served from /app by FastAPI; the dev server proxies the API
// so the same same-origin URLs work in both.
export default defineConfig({
  base: "/app/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:7860", ws: true },
    },
  },
});
