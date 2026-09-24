/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs here (port 5173) and the API on 8000. The
// proxy forwards API calls, so the browser sees one origin — the same
// shape as production, where the API serves the built UI itself.
const api = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/chat": api, "/feedback": api, "/health": api },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
    // Browser layout tests belong to Playwright (npm run test:e2e).
    exclude: ["e2e/**", "node_modules/**"],
  },
});
