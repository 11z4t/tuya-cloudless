// @ts-check
/**
 * Global setup: inject HA long-lived access token into browser localStorage
 * so all E2E tests start pre-authenticated.
 *
 * Requires: HA_TOKEN env var (long-lived access token)
 * Reads:    HA_URL env var (default: http://192.168.9.10:8123)
 */

const { chromium } = require("@playwright/test");
const path = require("path");
const fs = require("fs");

const STORAGE_STATE = path.join(__dirname, "storageState.json");

async function globalSetup(_config) {
  const haToken = process.env.HA_TOKEN || "";
  const haUrl = process.env.HA_URL || "http://192.168.9.10:8123";

  if (!haToken) {
    fs.writeFileSync(STORAGE_STATE, JSON.stringify({ cookies: [], origins: [] }));
    console.log("[auth.setup] HA_TOKEN not set — tests will use HA_USER/HA_PASS login");
    return;
  }

  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Navigate to HA and wait for it to fully settle (including auth redirect)
    await page.goto(haUrl, { waitUntil: "networkidle", timeout: 20000 });

    // Inject the long-lived token as hassTokens in localStorage
    await page.evaluate(
      ([url, token]) => {
        const hassTokens = JSON.stringify({
          access_token: token,
          token_type: "Bearer",
          expires_in: 31536000,
          expires_at: Date.now() / 1000 + 31536000,
          hassUrl: url,
          clientId: url + "/",
          state: null,
          refresh_token: "",
        });
        localStorage.setItem("hassTokens", hassTokens);
      },
      [haUrl, haToken],
    );

    // Reload so HA picks up the injected auth
    await page.goto(haUrl, { waitUntil: "networkidle", timeout: 20000 });

    try {
      await page.waitForSelector("home-assistant", { timeout: 20000 });
    } catch {
      const url = page.url();
      if (url.includes("/auth/")) {
        throw new Error(`Auth injection failed — still on login page: ${url}`);
      }
    }

    await context.storageState({ path: STORAGE_STATE });
    console.log(`[auth.setup] Authenticated storageState saved: ${STORAGE_STATE}`);
  } finally {
    await browser.close();
  }
}

module.exports = globalSetup;
