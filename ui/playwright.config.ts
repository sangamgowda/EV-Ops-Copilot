import { defineConfig } from "@playwright/test";

// Layout tests run the real UI in a real browser (Chromium) against the
// Vite dev server. /chat is answered by a recorded stream inside each
// test, so no API, database or LLM is needed.
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  reporter: "list",
  use: { baseURL: "http://localhost:5174" },
  webServer: {
    command: "npx vite --port 5174 --strictPort",
    port: 5174,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
