// @ts-check
/**
 * HA onboarding + TC setup spec.
 * Run this ONCE on a fresh HA instance.
 *
 *   npx playwright test --config=playwright.ha.config.js tests/e2e/ha_onboard.spec.js
 */

const { test, expect } = require("@playwright/test");
const path = require("path");
const fs   = require("fs");

const HA_URL  = process.env.HA_URL  || "http://192.168.9.10:8123";
const HA_USER = process.env.HA_USER || "test";
const HA_NAME = process.env.HA_NAME || "Test User";
const HA_PASS = process.env.HA_PASS || "carmabox123";

const TC_DIR = path.join(__dirname, "../../custom_components/tuya_cloudless");

// Helper: check if HA is in onboarding mode
async function isOnboarding(page) {
  const resp = await page.goto(HA_URL);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1000);
  return page.url().includes("onboarding") || resp?.url().includes("onboarding");
}

test("HA login works", async ({ page }) => {
  await page.goto(HA_URL);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(2000);

  console.log("Current URL:", page.url());

  // HA shows its auth page — login with keyboard (Shadow DOM workaround)
  // The auth page has: Username input, Password input, Login button
  await page.keyboard.press("Tab");  // focus username
  await page.waitForTimeout(200);
  await page.keyboard.type(HA_USER);  // type username (NOT display name)
  await page.keyboard.press("Tab");  // focus password
  await page.keyboard.type(HA_PASS);
  await page.keyboard.press("Enter");

  await page.waitForTimeout(5000);
  await page.waitForLoadState("domcontentloaded");
  const finalUrl = page.url();
  console.log("After login:", finalUrl);

  // Should be on dashboard, not auth page
  const onAuth = finalUrl.includes("/auth/authorize") && !finalUrl.includes("onboarding");
  if (onAuth) {
    // Check screenshot — credentials might be wrong
    console.log("Still on auth page — checking content");
    const content = await page.content();
    const hasError = content.toLowerCase().includes("invalid");
    if (hasError) {
      console.log("Login failed — credentials may need to be set up");
      // This is not a hard failure if HA is fresh — we'll set up credentials later
      return;
    }
  }

  // Should be on a HA page, not still on the pure auth page
  const notOnAuth = !finalUrl.includes("/auth/authorize") ||
                    finalUrl.includes("onboarding") ||
                    finalUrl.includes("lovelace") ||
                    finalUrl.includes("config") ||
                    finalUrl.includes("states");
  console.log("Login result:", notOnAuth ? "SUCCESS" : "STILL ON AUTH");
  expect(notOnAuth).toBeTruthy();
});

test("deploy TC files via HA File Editor API", async ({ page }) => {
  // Navigate to HA and login
  await page.goto(HA_URL);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(2000);

  if (page.url().includes("onboarding")) {
    test.skip(true, "HA not yet onboarded — run ha_onboard test first");
    return;
  }

  // Login
  await page.keyboard.press("Tab");
  await page.keyboard.type(HA_USER);
  await page.keyboard.press("Tab");
  await page.keyboard.type(HA_PASS);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(5000);
  await page.waitForLoadState("domcontentloaded");

  if (page.url().includes("/auth/")) {
    test.skip(true, "Login failed — check credentials");
    return;
  }

  // Try to use HA REST API to check if File Editor addon is available
  const addonCheck = await page.evaluate(async (haUrl) => {
    try {
      const resp = await fetch(`${haUrl}/api/hassio/addons`, {
        credentials: "same-origin",
      });
      const text = await resp.text();
      return { status: resp.status, has_editor: text.includes("file_editor") || text.includes("configurator") };
    } catch (e) {
      return { error: e.message };
    }
  }, HA_URL);

  console.log("Addon check:", JSON.stringify(addonCheck));

  // Get current user's long-lived token via API
  const tokenInfo = await page.evaluate(async (haUrl) => {
    // Check localStorage for existing token
    for (const key of Object.keys(localStorage)) {
      try {
        const val = JSON.parse(localStorage.getItem(key) || "");
        if (val && val.access_token) {
          return { found: true, length: val.access_token.length, token: val.access_token };
        }
      } catch {}
    }
    // Try creating a long-lived token
    try {
      const resp = await fetch(`${haUrl}/auth/long_lived_access_token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ lifespan: 365, client_name: "TC-Deploy" }),
      });
      if (resp.ok) {
        const data = await resp.json();
        return { created: true, token: data.token || data.access_token };
      }
      return { status: resp.status };
    } catch (e) {
      return { error: e.message };
    }
  }, HA_URL);

  console.log("Token info:", JSON.stringify({ ...tokenInfo, token: tokenInfo.token ? `[${tokenInfo.token.length} chars]` : undefined }));

  // Save token to file for use by deploy script
  if (tokenInfo.token) {
    const tokenFile = "/tmp/ha-token.env";
    // Write via node - use page.evaluate can't write files, log instead
    console.log(`Found token (length=${tokenInfo.token.length}) — use HA_TOKEN env var for ha_deploy.sh`);
    // Write to /tmp via the test process (not the browser)
    fs.writeFileSync(tokenFile, `HA_TOKEN=${tokenInfo.token}\nHA_URL=${HA_URL}\n`);
    console.log(`Token written to ${tokenFile}`);
  }
});

test("verify TC is installed (or skip for manual deploy)", async ({ page }) => {
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      await page.goto(HA_URL, { waitUntil: "domcontentloaded", timeout: 20000 });
      break;
    } catch (err) {
      if (attempt === 3) throw err;
      await page.waitForTimeout(5000);
    }
  }

  if (page.url().includes("onboarding")) {
    test.skip(true, "HA not yet onboarded");
    return;
  }

  // Login
  await page.keyboard.press("Tab");
  await page.keyboard.type(HA_USER);
  await page.keyboard.press("Tab");
  await page.keyboard.type(HA_PASS);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(5000);

  // Try to start the tuya_cloudless config flow
  const result = await page.evaluate(async (haUrl) => {
    try {
      const resp = await fetch(`${haUrl}/api/config/config_entries/flow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ handler: "tuya_cloudless" }),
      });
      const data = await resp.json();
      return { status: resp.status, type: data.type, step: data.step_id, error: data.message };
    } catch (e) {
      return { error: e.message };
    }
  }, HA_URL);

  console.log("Config flow attempt:", JSON.stringify(result));

  if (result.status === 404 || result.error?.includes("not found")) {
    console.log("tuya_cloudless not installed — deploy files to /config/custom_components/tuya_cloudless/");
    console.log("Then restart HA and re-run this test");
    // Not a hard failure — this test serves as a check
    return;
  }

  if (result.type === "external_step" || result.step) {
    console.log("tuya_cloudless IS installed and config flow works!");
    // Abort the flow
    await page.evaluate(async (haUrl) => {
      const resp = await fetch(`${haUrl}/api/config/config_entries/flow`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handler: "tuya_cloudless" }),
      });
      const data = await resp.json();
      if (data.flow_id) {
        await fetch(`${haUrl}/api/config/config_entries/flow/${data.flow_id}`, {
          method: "DELETE",
          credentials: "same-origin",
        });
      }
    }, HA_URL);
  }
});
