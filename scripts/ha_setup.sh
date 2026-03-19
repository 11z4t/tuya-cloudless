#!/usr/bin/env bash
# =============================================================================
# Tuya Cloudless — HA dev instance initial setup
#
# Completes HA onboarding, creates test user, and deploys tuya-cloudless.
# Run this ONCE on a fresh HA instance before ha_deploy.sh.
#
# Usage:
#   ./scripts/ha_setup.sh                     # dev-HA (192.168.9.10)
#   HA_HOST=192.168.5.22 ./scripts/ha_setup.sh
# =============================================================================
set -euo pipefail

HA_HOST="${HA_HOST:-192.168.9.10}"
HA_PORT="${HA_PORT:-8123}"
HA_URL="http://${HA_HOST}:${HA_PORT}"
HA_USER="${HA_USER:-test}"
HA_NAME="Test User"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

echo "═══ Tuya Cloudless — HA Dev Setup ═══"
echo "    Target: ${HA_URL}"
echo ""

# Write credentials to temp file
CREDS_FILE="/tmp/ha-setup-creds-$$.env"
trap "rm -f $CREDS_FILE" EXIT

# ── Check if HA needs onboarding ─────────────────────────────────
REDIRECT=$(curl -si -m 5 "${HA_URL}/" 2>/dev/null | grep -i "location:" | head -1 || echo "")
if echo "$REDIRECT" | grep -qi "onboarding"; then
  warn "HA is in onboarding mode — will complete setup"
else
  log "HA already onboarded — skipping onboarding step"
  SKIP_ONBOARD=true
fi

# ── Playwright-based onboarding ───────────────────────────────────
if [[ "${SKIP_ONBOARD:-false}" != "true" ]]; then
  echo ""
  echo "── Onboarding HA ──"
  warn "HA password will be set from HA_PASS env var (default: carmabox123)"

  # Write the setup script to a temp JS file
  ONBOARD_SCRIPT="/tmp/ha-onboard-$$.js"
  trap "rm -f $ONBOARD_SCRIPT $CREDS_FILE" EXIT

  cat > "$ONBOARD_SCRIPT" << 'JSEOF'
const { chromium } = require("playwright");

const HA_URL = process.env.HA_URL || "http://192.168.9.10:8123";
const HA_USER = process.env.HA_USER || "test";
const HA_NAME = process.env.HA_NAME || "Test User";
const HA_PASS = process.env.HA_PASS || "carmabox123";

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    console.log("Navigating to HA...");
    await page.goto(HA_URL, { timeout: 30000 });
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);

    const url = page.url();
    if (!url.includes("onboarding")) {
      console.log("HA already onboarded, URL:", url);
      process.exit(0);
    }

    console.log("Starting onboarding...");

    // Onboarding page - create user step
    // HA onboarding uses custom web components — use keyboard approach
    await page.keyboard.press("Tab");
    await page.waitForTimeout(500);

    // Try to find input fields
    const inputs = await page.locator("input").all();
    console.log("Inputs found:", inputs.length);

    if (inputs.length >= 3) {
      // Name, Username, Password, Confirm password fields
      await page.keyboard.type(HA_NAME);
      await page.keyboard.press("Tab");
      await page.keyboard.type(HA_USER);
      await page.keyboard.press("Tab");
      await page.keyboard.type(HA_PASS);
      await page.keyboard.press("Tab");
      await page.keyboard.type(HA_PASS);
      await page.keyboard.press("Enter");
    } else {
      // Try direct evaluation
      await page.evaluate(([user, name, pass]) => {
        const onboarding = document.querySelector("onboarding-create-user");
        if (onboarding) {
          const shadow = onboarding.shadowRoot;
          if (shadow) {
            const inputs = shadow.querySelectorAll("input");
            if (inputs[0]) inputs[0].value = name;
            if (inputs[1]) inputs[1].value = user;
            if (inputs[2]) inputs[2].value = pass;
            if (inputs[3]) inputs[3].value = pass;
          }
        }
      }, [HA_USER, HA_NAME, HA_PASS]);
      await page.keyboard.press("Enter");
    }

    await page.waitForTimeout(5000);
    await page.waitForLoadState("networkidle");

    // Handle subsequent onboarding steps (analytics, location, etc.)
    for (let i = 0; i < 5; i++) {
      const currentUrl = page.url();
      if (!currentUrl.includes("onboarding")) break;
      console.log("Still on onboarding, step", i + 1, "...");
      // Click "next" or skip buttons
      try {
        await page.click("mwc-button:last-of-type", { timeout: 3000 });
      } catch {
        await page.keyboard.press("Enter");
      }
      await page.waitForTimeout(2000);
    }

    const finalUrl = page.url();
    console.log("Final URL:", finalUrl);
    if (finalUrl.includes("onboarding")) {
      console.error("Still on onboarding after setup attempts");
      process.exit(1);
    }

    console.log("Onboarding complete!");
    process.exit(0);
  } catch (err) {
    console.error("Error during onboarding:", err.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
JSEOF

  # Run the onboarding script
  HA_URL="$HA_URL" HA_USER="$HA_USER" HA_NAME="$HA_NAME" \
    node "$ONBOARD_SCRIPT" || warn "Onboarding script had issues — HA may need manual setup"
fi

# ── Get a long-lived token ────────────────────────────────────────
echo ""
echo "── Getting access token ──"

# Use Playwright to log in and extract token from HA profile
TOKEN_SCRIPT="/tmp/ha-get-token-$$.js"
cat > "$TOKEN_SCRIPT" << 'JSEOF'
const { chromium } = require("playwright");

const HA_URL = process.env.HA_URL || "http://192.168.9.10:8123";
const HA_USER = process.env.HA_USER || "test";
const HA_PASS = process.env.HA_PASS || "carmabox123";
const CREDS_FILE = process.env.CREDS_FILE;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    await page.goto(HA_URL);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    if (page.url().includes("onboarding")) {
      console.log("Still on onboarding");
      process.exit(1);
    }

    // Login
    await page.keyboard.press("Tab");
    await page.keyboard.type(HA_USER);
    await page.keyboard.press("Tab");
    await page.keyboard.type(HA_PASS);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(5000);
    await page.waitForLoadState("networkidle");

    // Create a long-lived access token via HA API (needs auth)
    // Use the browser's authenticated session to call the auth API
    const token = await page.evaluate(async (haUrl) => {
      // HA stores auth token in localStorage or cookies
      // Try to get it from localStorage
      const keys = Object.keys(localStorage);
      for (const key of keys) {
        try {
          const val = localStorage.getItem(key);
          if (val && val.length > 100) {
            const parsed = JSON.parse(val);
            if (parsed.access_token) return parsed.access_token;
            if (parsed.token) return parsed.token;
          }
        } catch {}
      }

      // Try to create a long-lived token via API
      const resp = await fetch(`${haUrl}/auth/long_lived_access_token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ lifespan: 365, client_name: "TC-Deploy" }),
      });
      if (resp.ok) {
        const data = await resp.json();
        return data.token || data.access_token;
      }
      return null;
    }, HA_URL);

    if (token && CREDS_FILE) {
      const fs = require("fs");
      fs.writeFileSync(CREDS_FILE, `HA_TOKEN=${token}\n`);
      console.log(`Token saved (length=${token.length})`);
    } else if (token) {
      console.log(`TOKEN_LENGTH=${token.length}`);
    } else {
      console.log("Could not get token");
    }

    process.exit(0);
  } catch (err) {
    console.error("Error:", err.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
JSEOF

HA_URL="$HA_URL" HA_USER="$HA_USER" CREDS_FILE="$CREDS_FILE" \
  node "$TOKEN_SCRIPT" 2>&1 || warn "Could not retrieve token automatically"

if [[ -f "$CREDS_FILE" ]]; then
  log "Token saved to $CREDS_FILE"
  # Add it to ha-dev.env for future use
  grep "HA_TOKEN" "$CREDS_FILE" >> /tmp/ha-dev.env 2>/dev/null || true
fi

# ── Deploy tuya-cloudless ─────────────────────────────────────────
echo ""
echo "── Deploying tuya-cloudless ──"

# Try SSH deployment — if no SSH, provide instructions
HA_SSH_HOST=""
for user in hassio root admin ha; do
  if ssh -o ConnectTimeout=3 -o BatchMode=yes "${user}@${HA_HOST}" "echo ok" >/dev/null 2>&1; then
    HA_SSH_HOST="${user}@${HA_HOST}"
    log "SSH access: $HA_SSH_HOST"
    break
  fi
done

if [[ -n "$HA_SSH_HOST" ]]; then
  DEST=$(ssh "$HA_SSH_HOST" "test -d /config/custom_components && echo /config || echo /homeassistant" 2>/dev/null)
  ssh "$HA_SSH_HOST" "mkdir -p ${DEST}/custom_components/tuya_cloudless/" 2>/dev/null
  rsync -az --delete --exclude='__pycache__' --exclude='*.pyc' \
    --no-group --no-owner --chmod=ugo=rwX \
    "${REPO_DIR}/custom_components/tuya_cloudless/" \
    "${HA_SSH_HOST}:${DEST}/custom_components/tuya_cloudless/" && \
    log "Files synced → ${DEST}/custom_components/tuya_cloudless/" || \
    warn "rsync failed"
else
  warn "No SSH access to ${HA_HOST} — deploy manually:"
  echo "  1. scp -r ${REPO_DIR}/custom_components/tuya_cloudless/ hassio@${HA_HOST}:/config/custom_components/"
  echo "  2. Or install from HACS: https://github.com/11z4t/tuya-cloudless"
fi

echo ""
echo "═══ Setup complete ═══"
echo "Next: ./scripts/ha_deploy.sh --skip-restart"
