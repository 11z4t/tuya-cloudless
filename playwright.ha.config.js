// @ts-check
/**
 * Playwright config for E2E tests against a real Home Assistant instance.
 *
 * Dev-HA:  http://192.168.9.10:8123 (user=test, configurable via env)
 * Prod-HA: set HA_URL env var to override
 *
 * Run:
 *   npx playwright test --config=playwright.ha.config.js
 *   HA_URL=http://192.168.9.10:8123 npx playwright test --config=playwright.ha.config.js
 */
/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: "./tests/e2e",
  timeout: 60_000,

  // Auth setup: injects HA_TOKEN into browser localStorage before any test runs
  globalSetup: "./tests/e2e/auth.setup.js",

  // Retry — HA can briefly refuse connections during periodic internal tasks
  retries: 2,

  // Single worker to avoid HA connection conflicts
  workers: 1,

  use: {
    headless: true,
    browserName: "chromium",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // Reuse authenticated browser state created by globalSetup
    storageState: "./tests/e2e/storageState.json",
  },
  reporter: [["line"]],
};

module.exports = config;
