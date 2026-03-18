// @ts-check
/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: "./pairing_ui/tests/playwright",
  timeout: 15_000,
  use: {
    headless: true,
    // All routes are mocked — no real server needed
    baseURL: "http://tuya-cloudless-test.local",
    // Chromium is the only browser that supports Web Bluetooth
    browserName: "chromium",
    // Disable CSP so inline mocks work
    bypassCSP: true,
    // Capture screenshots on failure
    screenshot: "only-on-failure",
  },
  reporter: [["line"]],
};

module.exports = config;
