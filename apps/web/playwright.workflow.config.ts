import { defineConfig, devices } from "@playwright/test";
import { randomUUID } from "node:crypto";

// Workflow specs get their own backend/database: the shared login rate limit
// (10 attempts per IP within 5 minutes) is product behavior, so suites must
// not compete for one window. Ports differ from the identity suite on purpose.
process.env.RISKLAB_E2E_RUN_ID ??= randomUUID();

export default defineConfig({
  testDir: "./tests",
  testMatch: "workflow.spec.ts",
  workers: 1,
  globalTeardown: "./tests/teardown.ts",
  use: { baseURL: "http://127.0.0.1:13001", trace: "retain-on-failure" },
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
      url: "http://127.0.0.1:18001/api/v1/health/ready",
      timeout: 300_000,
      reuseExistingServer: false,
      env: {
        RISKLAB_E2E_RUN_ID: process.env.RISKLAB_E2E_RUN_ID,
        RISKLAB_TEST_DATABASE_URL: "",
        RISKLAB_BROWSER_BACKEND_PORT: "18001",
        RISKLAB_BROWSER_WEB_PORT: "13001",
      },
    },
    {
      command: "npm run dev -- --port 13001",
      url: "http://127.0.0.1:13001",
      env: {
        RISKLAB_API_ORIGIN: "http://127.0.0.1:18001",
        RISKLAB_E2E_BASE_URL: "http://127.0.0.1:13001",
        NEXT_TELEMETRY_DISABLED: "1",
      },
      reuseExistingServer: false,
    },
  ],
});
