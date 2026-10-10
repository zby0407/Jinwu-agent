import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:4717";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    serviceWorkers: "block",
    trace: "retain-on-failure",
  },
  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
    ? undefined
    : {
        command: "npx next dev --hostname 127.0.0.1 --port 4717",
        url: baseURL,
        // A regression run must never attach to a user's existing service.
        reuseExistingServer: false,
        timeout: 120_000,
      },
});
