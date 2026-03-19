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
  use: {
    headless: true,
    browserName: "chromium",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  reporter: [["line"]],
};

module.exports = config;
