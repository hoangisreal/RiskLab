import { defineConfig, devices } from "@playwright/test";
import { randomUUID } from "node:crypto";

// Teardown may run after the backend process group was killed; only this run's containers qualify.
process.env.RISKLAB_E2E_RUN_ID ??= randomUUID();

export default defineConfig({
  testDir: "./tests",
  // Workflow specs run under playwright.workflow.config.ts on their own
  // backend so the shared login rate-limit window stays product-realistic.
  testIgnore: "workflow.spec.ts",
  workers: 1,
  globalTeardown: "./tests/teardown.ts",
  use: { baseURL: "http://127.0.0.1:13000", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" },
    },
  ],
  webServer: [
    {
      command: "uv run python -m scripts.browser_backend",
      cwd: "../..",
      url: "http://127.0.0.1:18000/api/v1/health/ready",
      timeout: 300_000,
      reuseExistingServer: false,
      env: {
        RISKLAB_E2E_RUN_ID: process.env.RISKLAB_E2E_RUN_ID,
        RISKLAB_TEST_DATABASE_URL: "",
      },
    },
    {
      command: "npm run dev -- --port 13000",
      url: "http://127.0.0.1:13000",
      env: {
        RISKLAB_API_ORIGIN: "http://127.0.0.1:18000",
        NEXT_TELEMETRY_DISABLED: "1",
      },
      reuseExistingServer: false,
    },
  ],
});
