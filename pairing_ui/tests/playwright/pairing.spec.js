// @ts-check
/**
 * Playwright E2E tests for the Tuya Cloudless pairing UI.
 *
 * All HTTP requests are intercepted by Playwright — no real server is needed.
 * Web Bluetooth and EventSource are mocked via page.addInitScript().
 *
 * Flow under test:
 *   Step 1 (WiFi credentials) → Step 2 (BLE pairing) → Step 3 (Done)
 */

const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const UI_DIR = path.join(__dirname, "../../../custom_components/tuya_cloudless/pairing_ui");
const BASE = "http://tuya-cloudless-test.local";

// ── Fixtures ──────────────────────────────────────────────────────────────────

/**
 * Wire up Playwright route mocks so the page loads from disk.
 * API endpoints return configurable JSON via `opts`.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object} [opts]
 * @param {object}   [opts.config]   - /api/provision/config response
 * @param {string[]} [opts.ssids]    - /api/provision/wifi-scan ssids
 * @param {object}   [opts.activate] - activation event payload (or null to suppress)
 */
async function setupRoutes(page, opts = {}) {
  const config = opts.config ?? {
    activator_url: BASE,
    events_url: BASE + "/api/provision/events",
  };
  const ssids = opts.ssids ?? ["HomeNet", "GuestNet", "WorkNet"];
  // tuya_aps: Tuya devices found in AP mode — empty by default (no devices nearby)
  const tuyaAps = opts.tuya_aps ?? [];

  // Serve the HTML page (matches / with or without query params)
  const htmlBody = fs.readFileSync(path.join(UI_DIR, "index.html"));
  await page.route(
    (url) => url.hostname === "tuya-cloudless-test.local" && url.pathname === "/",
    (route) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: htmlBody })
  );

  // Serve app.js
  await page.route(BASE + "/static/app.js", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: fs.readFileSync(path.join(UI_DIR, "app.js")),
    })
  );

  // Serve i18n files
  await page.route(BASE + "/static/i18n/**", (route) => {
    const lang = route.request().url().split("/").pop();
    const filePath = path.join(UI_DIR, "i18n", lang);
    if (fs.existsSync(filePath)) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: fs.readFileSync(filePath),
      });
    } else {
      route.fulfill({ status: 404 });
    }
  });

  // Serve icon (return minimal 1-pixel PNG)
  await page.route(BASE + "/static/icon.png", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: Buffer.alloc(0) })
  );

  // Mock /api/provision/config
  await page.route(BASE + "/api/provision/config", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(config) })
  );

  // Mock /api/provision/wifi-scan — tuya_aps allows testing the dropdown filter
  const wifiScanTuyaAps = opts.wifi_scan_tuya_aps ?? [];
  await page.route(BASE + "/api/provision/wifi-scan", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ssids, tuya_aps: wifiScanTuyaAps, current_ssid: null }),
    })
  );

  // Mock QR endpoint
  await page.route(BASE + "/api/provision/qr.svg", (route) =>
    route.fulfill({ status: 503, body: "" })
  );

  // Mock /api/provision/quick-scan (auto device discovery — no Tuya APs by default)
  await page.route(BASE + "/api/provision/quick-scan", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tuya_aps: tuyaAps }) })
  );
}

/**
 * Inject a mock `navigator.bluetooth` that auto-responds to the BLE handshake
 * and WiFi config exchange, then fires an SSE "activated" event.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object} [activation] - SSE payload to fire, or null to skip
 */
async function mockBle(page, activation = null) {
  await page.addInitScript(
    ({ activation }) => {
      // Pretend we're in a secure context so btn-pair is not disabled
      Object.defineProperty(window, "isSecureContext", { get: () => true });

      // ── Mock EventSource so SSE "activated" event fires automatically ──────
      window._bleActivation = activation;
      const _origES = window.EventSource;
      window.EventSource = class MockEventSource {
        constructor(url) {
          this.url = url;
          this.readyState = 1;
          this._cbs = {};
        }
        addEventListener(evt, cb) {
          if (!this._cbs[evt]) this._cbs[evt] = [];
          this._cbs[evt].push(cb);
          if (evt === "activated" && window._bleActivation) {
            setTimeout(() => {
              cb({ data: JSON.stringify(window._bleActivation) });
            }, 300);
          }
        }
        set onerror(fn) { this._onerror = fn; }
        close() { this.readyState = 2; }
      };

      // ── Mock navigator.bluetooth ───────────────────────────────────────────
      // Build a fake BLE device that:
      //   1. Responds to handshake (CMD_HANDSHAKE=0x00) with a 16-byte nonce
      //   2. Responds to WiFi config (CMD_WIFI_CONFIG=0x01) with a simple ACK
      function crc16Modbus(data) {
        let crc = 0xFFFF;
        for (const byte of data) {
          crc ^= byte;
          for (let i = 0; i < 8; i++) {
            if (crc & 1) crc = (crc >>> 1) ^ 0xA001;
            else crc >>>= 1;
          }
        }
        return crc & 0xFFFF;
      }
      function buildFrame(cmd, seq, payload) {
        const header = new Uint8Array(8);
        header[0] = 0x55; header[1] = 0xAA;
        header[2] = 0x04; // PROTOCOL_VER
        header[3] = cmd;
        header[4] = (seq >> 8) & 0xff; header[5] = seq & 0xff;
        header[6] = (payload.length >> 8) & 0xff; header[7] = payload.length & 0xff;
        const body = new Uint8Array(header.length + payload.length);
        body.set(header);
        body.set(payload, header.length);
        // Compute real CRC-16/MODBUS and append LE
        const crc = crc16Modbus(body);
        const frame = new Uint8Array(body.length + 2);
        frame.set(body);
        frame[body.length]     = crc & 0xff;
        frame[body.length + 1] = (crc >> 8) & 0xff;
        return frame;
      }

      let _notifyCb = null;
      let _firstChunkData = null;  // cmd is at byte 3 of the FIRST chunk's frame data

      const fakeChar = {
        startNotifications: async () => {},
        addEventListener: (_evt, cb) => { _notifyCb = cb; },
        removeEventListener: () => {},  // required: _cleanupNotify() calls this
        writeValueWithoutResponse: async (chunk) => {
          const chunkNo = chunk[0], total = chunk[1];
          // Save first chunk — cmd byte (frame header offset 3) is always here
          if (chunkNo === 0) _firstChunkData = chunk.slice(2);
          const isLastChunk = chunkNo + 1 === total;
          if (!isLastChunk || !_notifyCb) return;

          // Read cmd from the first chunk's frame data (reliable for any payload size)
          const cmd = (_firstChunkData && _firstChunkData.length > 3) ? _firstChunkData[3] : 0xff;

          // Build a response: handshake → nonce reply, wifi → ACK (cmd=0x02)
          let respPayload;
          let respCmd;
          if (cmd === 0x00) {
            // Handshake — reply with 16-byte device nonce
            respPayload = new Uint8Array(16);
            respCmd = 0x00;
          } else {
            // WiFi config ACK
            respPayload = new Uint8Array(0);
            respCmd = 0x02;
          }
          const frame = buildFrame(respCmd, 0, respPayload);
          // Wrap in single chunk [0, 1, ...frame]
          const respChunk = new Uint8Array(2 + frame.length);
          respChunk[0] = 0; respChunk[1] = 1;
          respChunk.set(frame, 2);

          setTimeout(() => {
            if (_notifyCb) {
              _notifyCb({ target: { value: { buffer: respChunk.buffer } } });
            }
          }, 1);  // 1ms — must fire well before the SSE_TIMEOUT_MS speedup (50ms)
        },
      };

      const fakeService = {
        getCharacteristic: async () => fakeChar,
      };

      const fakeServer = {
        connected: true,
        disconnect: () => { fakeServer.connected = false; },
        getPrimaryService: async () => fakeService,
      };

      const fakeDevice = {
        id: "fake-ble-device",
        name: "Tuya Device",
        gatt: {
          connected: false,
          connect: async () => { fakeDevice.gatt.connected = true; return fakeServer; },
        },
      };

      Object.defineProperty(navigator, "bluetooth", {
        value: {
          requestDevice: async () => fakeDevice,
        },
        configurable: true,
        writable: false,
      });
    },
    { activation }
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function loadPage(page) {
  await page.goto(BASE + "/");
  // Wait for app.js to initialise (strings applied)
  await page.waitForFunction(() => document.title !== "Tuya Cloudless — Pair Device" || document.getElementById("step1-title").textContent !== "");
}

/**
 * After loadPage(), the device discovery panel (#panel-devices) is shown first.
 * Click the BLE scan button to navigate to the WiFi credentials panel (#panel-wifi).
 */
async function navigateToCredentials(page) {
  await page.locator("#btn-ble-scan").click();
  await page.waitForSelector("#panel-wifi:not(.hidden)");
}

// ── Tests: Step 1 — WiFi form ─────────────────────────────────────────────────

test.describe("Step 1 — WiFi credentials form", () => {
  test("page loads with device discovery panel visible, WiFi panel hidden", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    await expect(page.locator("#panel-devices")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeHidden();
    await expect(page.locator("#panel-ble")).toBeHidden();
    await expect(page.locator("#panel-done")).toBeHidden();
  });

  test("clicking BLE scan button navigates to WiFi credentials panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    await navigateToCredentials(page);

    await expect(page.locator("#panel-devices")).toBeHidden();
    await expect(page.locator("#panel-wifi")).toBeVisible();
    await expect(page.locator("#panel-ble")).toBeHidden();
  });

  test("step counter shows STEP 1 OF 2 on device panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    const counter = page.locator("#step-counter");
    await expect(counter).toBeVisible();
    await expect(counter).toContainText("1");
  });

  test("clicking Next without SSID shows error", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);

    // SSID field is readonly+empty by default — just click Next to trigger validation
    await page.locator("#btn-next").click();

    await expect(page.locator("#s1-error")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeVisible();
    await expect(page.locator("#panel-ble")).toBeHidden();
  });

  test("entering SSID and clicking Next advances to BLE panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyHomeNetwork");
    await page.locator("#btn-next").click();

    await expect(page.locator("#panel-wifi")).toBeHidden();
    await expect(page.locator("#panel-ble")).toBeVisible();
    const counter = page.locator("#step-counter");
    // Device discovery = step 1, credentials = step 2, BLE = step 3
    await expect(counter).toContainText("3");
  });
});

// ── Tests: WiFi scan ──────────────────────────────────────────────────────────

test.describe("WiFi scan dropdown", () => {
  test("scan button fetches networks and shows dropdown", async ({ page }) => {
    await setupRoutes(page, { ssids: ["HomeNet", "GuestNet", "WorkNet"] });
    await loadPage(page);
    await navigateToCredentials(page);

    await expect(page.locator("#wifi-dropdown")).toBeHidden();
    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator("#wifi-dropdown")).toBeVisible();

    const options = page.locator(".wifi-option");
    await expect(options).toHaveCount(3);
    await expect(options.nth(0)).toContainText("HomeNet");
    await expect(options.nth(1)).toContainText("GuestNet");
    await expect(options.nth(2)).toContainText("WorkNet");
  });

  test("clicking a network fills the SSID field and hides dropdown", async ({ page }) => {
    await setupRoutes(page, { ssids: ["HomeNet", "OfficeNet"] });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    await page.locator(".wifi-option", { hasText: "OfficeNet" }).click();

    await expect(page.locator("#ssid")).toHaveValue("OfficeNet");
    await expect(page.locator("#wifi-dropdown")).toBeHidden();
  });

  test("empty scan result shows 'No networks found'", async ({ page }) => {
    await setupRoutes(page, { ssids: [] });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator(".wifi-option.muted")).toBeVisible();
    await expect(page.locator(".wifi-option.muted")).toContainText("No networks found");
  });

  test("clicking outside dropdown closes it", async ({ page }) => {
    await setupRoutes(page, { ssids: ["HomeNet"] });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator("#wifi-dropdown")).toBeVisible();

    // Click somewhere outside the dropdown
    await page.locator("footer").click();
    await expect(page.locator("#wifi-dropdown")).toBeHidden();
  });

  test("Tuya AP SSIDs are filtered out of WiFi dropdown", async ({ page }) => {
    await setupRoutes(page, {
      ssids: ["HomeNet", "SmartLife_AB12", "GuestNet"],
      wifi_scan_tuya_aps: [{ ssid: "SmartLife_AB12" }],
    });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator("#wifi-dropdown")).toBeVisible();

    const options = page.locator(".wifi-option");
    // SmartLife_AB12 should NOT appear in the home network dropdown
    await expect(options).toHaveCount(2);  // HomeNet + GuestNet only
    await expect(page.locator("#wifi-dropdown")).not.toContainText("SmartLife_AB12");
  });
});

// ── Tests: WiFi dropdown keyboard navigation ───────────────────────────────────

test.describe("WiFi dropdown keyboard navigation", () => {
  test("ArrowDown wraps from last item to first", async ({ page }) => {
    await setupRoutes(page, { ssids: ["Net-A", "Net-B", "Net-C"] });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator("#wifi-dropdown")).toBeVisible();

    const options = page.locator(".wifi-option[tabindex='0']");
    await options.nth(0).focus();
    // Move to last item
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await expect(options.nth(2)).toBeFocused();
    // Wrap from last to first
    await page.keyboard.press("ArrowDown");
    await expect(options.nth(0)).toBeFocused();
  });

  test("ArrowUp wraps from first item to last", async ({ page }) => {
    await setupRoutes(page, { ssids: ["Net-A", "Net-B", "Net-C"] });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    const options = page.locator(".wifi-option[tabindex='0']");
    await options.nth(0).focus();
    // Wrap from first to last
    await page.keyboard.press("ArrowUp");
    await expect(options.nth(2)).toBeFocused();
  });

  test("Escape closes dropdown and returns focus to SSID input", async ({ page }) => {
    await setupRoutes(page, { ssids: ["HomeNet"] });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator("#wifi-dropdown")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#wifi-dropdown")).toBeHidden();
    await expect(page.locator("#ssid")).toBeFocused();
  });

  test("Enter key selects focused item", async ({ page }) => {
    await setupRoutes(page, { ssids: ["Net-A", "Net-B"] });
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#btn-wifi-scan").click();
    const options = page.locator(".wifi-option[tabindex='0']");
    await options.nth(1).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#ssid")).toHaveValue("Net-B");
    await expect(page.locator("#wifi-dropdown")).toBeHidden();
  });
});

// ── Tests: SSID pre-fill ──────────────────────────────────────────────────────

test.describe("SSID pre-fill", () => {
  test("server config default_ssid pre-fills the SSID input", async ({ page }) => {
    await setupRoutes(page, {
      config: {
        activator_url: BASE,
        events_url: BASE + "/api/provision/events",
        default_ssid: "HA-Network",
      },
    });
    await loadPage(page);

    await expect(page.locator("#ssid")).toHaveValue("HA-Network");
  });

  test("localStorage SSID pre-fills when no server SSID", async ({ page }) => {
    await setupRoutes(page);

    // Inject saved SSID into localStorage before page init
    await page.addInitScript(() => {
      localStorage.setItem(
        "tuya_cloudless_last_ssid",
        JSON.stringify({ ssid: "PreviousNet", saved_at: Date.now() })
      );
    });

    await loadPage(page);
    await expect(page.locator("#ssid")).toHaveValue("PreviousNet");
  });

  test("server SSID takes priority over localStorage SSID", async ({ page }) => {
    await setupRoutes(page, {
      config: {
        activator_url: BASE,
        events_url: BASE + "/api/provision/events",
        default_ssid: "ServerNet",
      },
    });

    await page.addInitScript(() => {
      localStorage.setItem(
        "tuya_cloudless_last_ssid",
        JSON.stringify({ ssid: "StoredNet", saved_at: Date.now() })
      );
    });

    await loadPage(page);
    await expect(page.locator("#ssid")).toHaveValue("ServerNet");
  });

  test("expired localStorage SSID is not restored", async ({ page }) => {
    await setupRoutes(page);

    await page.addInitScript(() => {
      // saved_at 91 days ago
      const OLD = Date.now() - 91 * 24 * 60 * 60 * 1000;
      localStorage.setItem(
        "tuya_cloudless_last_ssid",
        JSON.stringify({ ssid: "OldNet", saved_at: OLD })
      );
    });

    await loadPage(page);
    // Should be empty (or placeholder text, not "OldNet")
    await expect(page.locator("#ssid")).not.toHaveValue("OldNet");
  });
});

// ── Tests: Browser compatibility ──────────────────────────────────────────────

test.describe("Browser compatibility", () => {
  test("missing navigator.bluetooth shows warn-browser and disables pair button", async ({
    page,
  }) => {
    await setupRoutes(page);

    // Remove bluetooth from navigator
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "bluetooth", {
        value: undefined,
        configurable: true,
        writable: true,
      });
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#warn-browser")).toBeVisible();
    await expect(page.locator("#btn-pair")).toBeDisabled();
  });

  test("unsupported browser (Firefox UA, no bluetooth) shows hard error panel", async ({
    page,
  }) => {
    await setupRoutes(page);

    // Simulate Firefox: no bluetooth AND non-Chrome/Edge/Safari UA
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "bluetooth", {
        value: undefined, configurable: true, writable: true,
      });
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        configurable: true, writable: true,
      });
    });

    await loadPage(page);

    await expect(page.locator("#err-browser-unsupported")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeHidden();
  });

  test("iOS: shows iOS-specific message, no QR, no Chrome button", async ({ page }) => {
    await setupRoutes(page);
    await page.addInitScript(() => {
      window._TEST_IS_IOS = true;
      Object.defineProperty(navigator, "bluetooth", {
        value: undefined, configurable: true, writable: true,
      });
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#warn-browser")).toBeVisible();
    const body = await page.locator("#err-browser-body").textContent();
    expect(body).toMatch(/iOS/i);
    await expect(page.locator("#qr-section")).toBeHidden();
    await expect(page.locator("#btn-open-chrome")).toHaveCount(0);
    await expect(page.locator("#btn-pair")).toBeDisabled();
  });

  test("Android: shows Chrome intent button, no QR section", async ({ page }) => {
    await setupRoutes(page);
    await page.addInitScript(() => {
      window._TEST_IS_ANDROID = true;
      Object.defineProperty(navigator, "bluetooth", {
        value: undefined, configurable: true, writable: true,
      });
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#warn-browser")).toBeVisible();
    await expect(page.locator("#btn-open-chrome")).toBeVisible();
    const href = await page.locator("#btn-open-chrome").getAttribute("href");
    expect(href).toMatch(/^intent:\/\//);
    expect(href).toContain("com.android.chrome");
    await expect(page.locator("#qr-section")).toBeHidden();
    await expect(page.locator("#btn-pair")).toBeDisabled();
  });

  test("Android Chrome button click shows fallback note after timeout", async ({ page }) => {
    await setupRoutes(page);
    await page.addInitScript(() => {
      window._TEST_IS_ANDROID = true;
      Object.defineProperty(navigator, "bluetooth", {
        value: undefined, configurable: true, writable: true,
      });
      // Speed up the 2500 ms timer
      const orig = window.setTimeout;
      window.setTimeout = (fn, delay, ...args) => orig(fn, delay > 100 ? 50 : delay, ...args);
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#btn-open-chrome")).toBeVisible();
    await expect(page.locator("#chrome-not-installed")).toBeHidden();
    await page.locator("#btn-open-chrome").click();
    await expect(page.locator("#chrome-not-installed")).toBeVisible({ timeout: 3000 });
  });
});

// ── Tests: Language picker ────────────────────────────────────────────────────

test.describe("Language picker", () => {
  test("switching to Swedish translates the step 1 title", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    const titleBefore = await page.locator("#step1-title").textContent();

    await page.locator("#lang-select").selectOption("sv");
    // Wait for strings to reload
    await page.waitForTimeout(300);

    const titleAfter = await page.locator("#step1-title").textContent();
    // Swedish title should differ from English
    expect(titleAfter).not.toBe(titleBefore);
    // Should not be empty or the raw key
    expect(titleAfter?.trim().length).toBeGreaterThan(0);
    expect(titleAfter).not.toBe("step1_title");
  });

  test("language selection is persisted to localStorage", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    await page.locator("#lang-select").selectOption("de");
    await page.waitForTimeout(200);

    const stored = await page.evaluate(() => localStorage.getItem("tc-lang"));
    expect(stored).toBe("de");
  });
});

// ── Tests: Full pairing flow (BLE + SSE) ─────────────────────────────────────

test.describe("Full pairing flow", () => {
  const ACTIVATION = {
    gw_id: "bf1234567890abcdef",
    local_key: "abcdef1234567890",
    ip_address: "192.168.1.55",
    token: null, // null → match any token
  };

  test("happy path: BLE + SSE activation shows done screen with device info", async ({
    page,
  }) => {
    await setupRoutes(page);
    await mockBle(page, ACTIVATION);
    await loadPage(page);
    await navigateToCredentials(page);

    // Step 1: enter WiFi credentials
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyHomeNet");
    await page.locator("#password").click();
    await page.locator("#password").fill("secret123");
    await page.locator("#btn-next").click();

    // Step 2: click Scan & Pair
    await expect(page.locator("#panel-ble")).toBeVisible();
    await page.locator("#btn-pair").click();

    // Step 3: done screen should appear (SSE fires after ~300ms, BLE exchange ~100ms)
    await expect(page.locator("#panel-done")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#panel-ble")).toBeHidden();
    await expect(page.locator("#panel-wifi")).toBeHidden();

    // Device info grid should contain the activation data
    await expect(page.locator("#result-grid")).toContainText(ACTIVATION.gw_id);
    await expect(page.locator("#result-grid")).toContainText(ACTIVATION.ip_address);
    await expect(page.locator("#result-grid")).toContainText(ACTIVATION.local_key);
  });

  test("done screen: 'Add to Home Assistant' button has correct deep-link href", async ({
    page,
  }) => {
    await setupRoutes(page);
    await mockBle(page, ACTIVATION);
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#panel-done")).toBeVisible({ timeout: 5000 });

    const href = await page.locator("#btn-add-ha").getAttribute("href");
    expect(href).toContain("domain=tuya_cloudless");
    expect(href).toContain("gw_id=" + ACTIVATION.gw_id);
    expect(href).toContain("local_key=" + ACTIVATION.local_key);
    expect(href).toContain("ip_address=" + ACTIVATION.ip_address);
  });

  test("done screen: missing ip_address in activation shows blank, not 'undefined'", async ({
    page,
  }) => {
    await setupRoutes(page);
    // Activation payload without ip_address
    const activationNoIp = { gw_id: "dev_noip", local_key: "abcdef1234567890" };
    await mockBle(page, activationNoIp);
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#panel-done")).toBeVisible({ timeout: 5000 });

    // result-grid must NOT contain the literal string "undefined"
    const gridText = await page.locator("#result-grid").textContent();
    expect(gridText).not.toContain("undefined");
  });

  test("successful pairing saves SSID to localStorage", async ({ page }) => {
    await setupRoutes(page);
    await mockBle(page, ACTIVATION);
    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("SavedNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#panel-done")).toBeVisible({ timeout: 5000 });

    const stored = await page.evaluate(() => {
      const raw = localStorage.getItem("tuya_cloudless_last_ssid");
      return raw ? JSON.parse(raw) : null;
    });
    expect(stored).not.toBeNull();
    expect(stored.ssid).toBe("SavedNet");
    expect(typeof stored.saved_at).toBe("number");
  });

  test("done screen: focus moves to step3-title heading on success", async ({ page }) => {
    await setupRoutes(page);
    await mockBle(page, ACTIVATION);
    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("HomeNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#panel-done")).toBeVisible({ timeout: 5000 });
    // Focus should move to the done screen title for screen reader accessibility
    const focusedId = await page.evaluate(() => document.activeElement?.id);
    expect(focusedId).toBe("step3-title");
  });

  test("password visibility resets to hidden when navigating back from BLE to credentials", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("HomeNet");

    // Show password
    await page.locator("#btn-pwd-toggle").click();
    await expect(page.locator("#password")).toHaveAttribute("type", "text");

    // Navigate to BLE panel — BLE method already selected by navigateToCredentials
    await page.locator("#btn-next").click();
    await expect(page.locator("#panel-ble")).toBeVisible();

    // Click Back from BLE panel — returns to credentials
    await page.locator("#btn-back-ble").click();
    await expect(page.locator("#panel-wifi")).toBeVisible();

    // Password should be reset to hidden
    await expect(page.locator("#password")).toHaveAttribute("type", "password");
    await expect(page.locator("#btn-pwd-toggle")).toHaveAttribute("aria-pressed", "false");
  });

  test("BLE scan cancelled shows warning (not error) and re-enables button", async ({
    page,
  }) => {
    await setupRoutes(page);

    // Mock BLE to throw NotFoundError (user clicked "Cancel" in browser dialog)
    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => true });
      Object.defineProperty(navigator, "bluetooth", {
        value: {
          requestDevice: async () => {
            const err = new Error("User cancelled");
            err.name = "NotFoundError";
            throw err;
          },
        },
        configurable: true,
      });
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    // Should show warning (status-warn), not error
    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-warn/);

    // Button should be re-enabled so user can retry
    await expect(page.locator("#btn-pair")).toBeEnabled();
  });

  test("BLE connection error shows error message and re-enables button", async ({ page }) => {
    await setupRoutes(page);

    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => true });
      Object.defineProperty(navigator, "bluetooth", {
        value: {
          requestDevice: async () => {
            throw new Error("GATT Server disconnected");
          },
        },
        configurable: true,
      });
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-error/);
    await expect(page.locator("#btn-pair")).toBeEnabled();
  });

  test("startNotifications() failure shows error and re-enables pair button", async ({ page }) => {
    await setupRoutes(page);

    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => true });
      const fakeChar = {
        startNotifications: async () => { throw new Error("Notifications not supported"); },
        addEventListener: () => {},
        removeEventListener: () => {},
        writeValueWithoutResponse: async () => {},
      };
      const fakeService = { getCharacteristic: async () => fakeChar };
      const fakeServer = {
        connected: true,
        disconnect: () => { fakeServer.connected = false; },
        getPrimaryService: async () => fakeService,
      };
      const fakeDevice = {
        id: "fake-notify-fail",
        name: "Tuya Device",
        gatt: { connected: false, connect: async () => { fakeDevice.gatt.connected = true; return fakeServer; } },
      };
      Object.defineProperty(navigator, "bluetooth", {
        value: { requestDevice: async () => fakeDevice },
        configurable: true,
      });
      window.EventSource = class MockESNever {
        constructor() { this.readyState = 1; }
        addEventListener() {}
        set onerror(_fn) {}
        close() { this.readyState = 2; }
      };
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    // Should show an error and re-enable the pair button
    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-error/);
    await expect(page.locator("#btn-pair")).toBeEnabled({ timeout: 3000 });
  });

  test("BLE SSE timeout shows warning message and re-enables pair button", async ({ page }) => {
    await setupRoutes(page);
    // BLE mock with no activation payload — SSE "activated" never fires
    await mockBle(page, null);
    // Speed up all long timers (> 100 ms) so SSE_TIMEOUT_MS fires quickly
    await page.addInitScript(() => {
      const orig = window.setTimeout;
      window.setTimeout = (fn, delay, ...args) =>
        orig(fn, delay > 100 ? 50 : delay, ...args);
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    // After timeout the error message should appear with status-warn
    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-warn/);
    // Pair button must be re-enabled so user can retry
    await expect(page.locator("#btn-pair")).toBeEnabled({ timeout: 3000 });
  });

  test("CMD_PAIR_FAIL response shows err_pair_fail message and re-enables button", async ({
    page,
  }) => {
    await setupRoutes(page);

    // Mock BLE device that sends CMD_PAIR_FAIL (cmd=0x05) on WiFi config
    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => true });

      function crc16Modbus(data) {
        let crc = 0xFFFF;
        for (const byte of data) {
          crc ^= byte;
          for (let i = 0; i < 8; i++) {
            if (crc & 1) crc = (crc >>> 1) ^ 0xA001;
            else crc >>>= 1;
          }
        }
        return crc & 0xFFFF;
      }
      function buildFrame(cmd, seq, payload) {
        const header = new Uint8Array(8);
        header[0] = 0x55; header[1] = 0xAA;
        header[2] = 0x04;
        header[3] = cmd;
        header[4] = (seq >> 8) & 0xff; header[5] = seq & 0xff;
        header[6] = (payload.length >> 8) & 0xff; header[7] = payload.length & 0xff;
        const body = new Uint8Array(header.length + payload.length);
        body.set(header); body.set(payload, header.length);
        const crc = crc16Modbus(body);
        const frame = new Uint8Array(body.length + 2);
        frame.set(body);
        frame[body.length]     = crc & 0xff;
        frame[body.length + 1] = (crc >> 8) & 0xff;
        return frame;
      }

      let _notifyCb = null;
      let _firstChunkData = null;  // cmd is always in the FIRST chunk at offset 3 of the frame
      const fakeChar = {
        startNotifications: async () => {},
        addEventListener: (_evt, cb) => { _notifyCb = cb; },
        removeEventListener: () => {},  // required: _cleanupNotify() calls this
        writeValueWithoutResponse: async (chunk) => {
          const chunkNo = chunk[0], total = chunk[1];
          // Save first chunk so we can read the frame cmd byte (offset 3 of frame = data[3])
          if (chunkNo === 0) _firstChunkData = chunk.slice(2);
          const isLastChunk = chunkNo + 1 === total;
          if (!isLastChunk || !_notifyCb) return;

          const cmd = (_firstChunkData && _firstChunkData.length > 3) ? _firstChunkData[3] : 0xff;

          let respPayload, respCmd;
          if (cmd === 0x00) {
            respPayload = new Uint8Array(16);  // handshake nonce
            respCmd = 0x00;
          } else {
            respPayload = new Uint8Array(0);
            respCmd = 0x05;  // CMD_PAIR_FAIL — device rejects credentials
          }
          const frame = buildFrame(respCmd, 0, respPayload);
          const respChunk = new Uint8Array(2 + frame.length);
          respChunk[0] = 0; respChunk[1] = 1;
          respChunk.set(frame, 2);
          setTimeout(() => {
            if (_notifyCb) _notifyCb({ target: { value: { buffer: respChunk.buffer } } });
          }, 1);  // 1ms — must fire well before the SSE_TIMEOUT_MS speedup (50ms)
        },
      };
      const fakeService = { getCharacteristic: async () => fakeChar };
      const fakeServer = {
        connected: true,
        disconnect: () => { fakeServer.connected = false; },
        getPrimaryService: async () => fakeService,
      };
      const fakeDevice = {
        id: "fake-ble-pair-fail",
        name: "Tuya Device",
        gatt: { connected: false, connect: async () => { fakeDevice.gatt.connected = true; return fakeServer; } },
      };
      Object.defineProperty(navigator, "bluetooth", {
        value: { requestDevice: async () => fakeDevice },
        configurable: true, writable: false,
      });

      // EventSource that never fires (connection succeeds but no activation)
      window.EventSource = class MockESNever {
        constructor() { this.readyState = 1; }
        addEventListener() {}
        set onerror(_fn) {}
        close() { this.readyState = 2; }
      };
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-error/);
    await expect(page.locator("#btn-pair")).toBeEnabled({ timeout: 3000 });
  });

  test("BLE frame with bad CRC shows error and re-enables pair button", async ({ page }) => {
    await setupRoutes(page);
    // Mock BLE device that sends a frame with a deliberately wrong CRC
    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => true });
      let _notifyCb = null;
      let _firstChunkData = null;
      const fakeChar = {
        startNotifications: async () => {},
        addEventListener: (_evt, cb) => { _notifyCb = cb; },
        removeEventListener: () => {},
        writeValueWithoutResponse: async (chunk) => {
          const chunkNo = chunk[0], total = chunk[1];
          if (chunkNo === 0) _firstChunkData = chunk.slice(2);
          if (chunkNo + 1 !== total || !_notifyCb) return;
          // Build a handshake response frame but corrupt the CRC
          const body = new Uint8Array(8 + 16);
          body[0] = 0x55; body[1] = 0xAA; body[2] = 0x04; body[3] = 0x00; // cmd=handshake
          body[6] = 0; body[7] = 16;  // payload length = 16
          const frame = new Uint8Array(body.length + 2);
          frame.set(body);
          frame[body.length] = 0xAB; frame[body.length + 1] = 0xCD;  // wrong CRC
          const respChunk = new Uint8Array(2 + frame.length);
          respChunk[0] = 0; respChunk[1] = 1;
          respChunk.set(frame, 2);
          setTimeout(() => {
            if (_notifyCb) _notifyCb({ target: { value: { buffer: respChunk.buffer } } });
          }, 1);
        },
      };
      const fakeService = { getCharacteristic: async () => fakeChar };
      const fakeServer = { getPrimaryService: async () => fakeService };
      const fakeDevice = {
        id: "crc-fail-device",
        name: "CRC Fail Device",
        gatt: { connect: async () => fakeServer },
        addEventListener: () => {}, removeEventListener: () => {},
      };
      Object.defineProperty(navigator, "bluetooth", {
        value: { requestDevice: async () => fakeDevice }, configurable: true,
      });
      window.EventSource = class MockES {
        constructor() { this.readyState = 1; }
        addEventListener() {} set onerror(_fn) {} close() {}
      };
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-error/);
    await expect(page.locator("#btn-pair")).toBeEnabled({ timeout: 3000 });
  });

  test("SSE onerror shows err_sse_lost error message and re-enables pair button", async ({
    page,
  }) => {
    await setupRoutes(page);
    await mockBle(page, null);  // BLE succeeds, no activation

    // Override EventSource to immediately trigger onerror
    await page.addInitScript(() => {
      window.EventSource = class MockESError {
        constructor() {
          this.readyState = 1;
          const self = this;
          setTimeout(() => { if (self._onerror) self._onerror(); }, 200);
        }
        addEventListener() {}
        set onerror(fn) { this._onerror = fn; }
        close() { this.readyState = 2; }
      };
    });

    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-error/);
    await expect(page.locator("#btn-pair")).toBeEnabled({ timeout: 3000 });
  });
});

// ── Tests: HA flow_id mode ────────────────────────────────────────────────────

test.describe("HA flow_id mode", () => {
  test("with flow_id in URL, done screen shows HA message instead of deep-link button", async ({
    page,
  }) => {
    const ACTIVATION = {
      gw_id: "dev001",
      local_key: "key0000000000001",
      ip_address: "10.0.0.1",
      token: null,
    };

    await setupRoutes(page);
    await mockBle(page, ACTIVATION);

    // Navigate with flow_id query param
    await page.goto(BASE + "/?flow_id=abc-ha-flow-123");
    await page.waitForFunction(() =>
      document.getElementById("step1-title").textContent !== ""
    );
    await page.locator("#btn-ble-scan").click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");

    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("FlowNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#panel-done")).toBeVisible({ timeout: 5000 });

    // HA flow message should be visible
    await expect(page.locator("#ha-flow-msg")).toBeVisible();
    // Deep-link button should remain hidden
    await expect(page.locator("#btn-add-ha")).toBeHidden();
  });
});

// ── Tests: HTTPS warning panel ────────────────────────────────────────────────

test.describe("HTTPS warning panel", () => {
  test("warn-https visible in BLE panel when isSecureContext is false", async ({ page }) => {
    await setupRoutes(page);

    // Override isSecureContext to simulate HTTP (non-secure) context
    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => false });
    });

    await loadPage(page);
    await navigateToCredentials(page);

    // Navigate to step 2 (BLE panel)
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#panel-ble")).toBeVisible();
    // warn-https must be visible inside the BLE panel
    await expect(page.locator("#warn-https")).toBeVisible();
  });

  test("btn-pair disabled when isSecureContext is false", async ({ page }) => {
    await setupRoutes(page);

    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => false });
    });

    await loadPage(page);
    await navigateToCredentials(page);

    // Navigate to BLE panel
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#panel-ble")).toBeVisible();
    await expect(page.locator("#btn-pair")).toBeDisabled();
  });

  test("warn-https hidden and btn-pair enabled when isSecureContext is true", async ({ page }) => {
    await setupRoutes(page);

    // Ensure secure context (the default in Playwright, but be explicit)
    await page.addInitScript(() => {
      Object.defineProperty(window, "isSecureContext", { get: () => true });
      // Stub bluetooth so we don't also get the browser warning
      if (!navigator.bluetooth) {
        Object.defineProperty(navigator, "bluetooth", {
          value: { requestDevice: async () => { throw new Error("stub"); } },
          configurable: true,
        });
      }
    });

    await loadPage(page);
    await navigateToCredentials(page);

    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#panel-ble")).toBeVisible();
    await expect(page.locator("#warn-https")).toBeHidden();
    // btn-pair enabled (assuming bluetooth stub present — no warn-browser either)
    await expect(page.locator("#btn-pair")).toBeEnabled();
  });
});

// ── Tests: Device discovery panel ────────────────────────────────────────────

test.describe("Device discovery panel", () => {
  test("panel-devices is shown on page load", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    await expect(page.locator("#panel-devices")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeHidden();
  });

  test("Tuya AP device card appears when quick-scan returns results", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await loadPage(page);

    await expect(page.locator(".device-card")).toBeVisible({ timeout: 3000 });
    await expect(page.locator(".device-card")).toContainText("SmartLife_AB12");
  });

  test("no device card when quick-scan returns empty", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [] });
    await loadPage(page);

    await expect(page.locator(".device-card")).not.toBeVisible({ timeout: 2000 });
  });

  test("BLE scan button navigates to credentials panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    await page.locator("#btn-ble-scan").click();

    await expect(page.locator("#panel-devices")).toBeHidden();
    await expect(page.locator("#panel-wifi")).toBeVisible();
  });

  test("Back button from credentials returns to device panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);

    await expect(page.locator("#panel-wifi")).toBeVisible();
    await page.locator("#btn-back").click();
    await expect(page.locator("#panel-devices")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeHidden();
  });

  test("Back button from BLE step returns to credentials panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);

    // Enter credentials and advance to BLE panel — BLE already selected by navigateToCredentials
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("HomeNet");
    await page.locator("#btn-next").click();
    await expect(page.locator("#panel-ble")).toBeVisible();

    // Click Back from BLE panel
    await page.locator("#btn-back-ble").click();
    await expect(page.locator("#panel-wifi")).toBeVisible();
    await expect(page.locator("#panel-ble")).toBeHidden();
  });

  test("BLE pair-status is cleared when re-navigating to BLE panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("HomeNet");
    // BLE already selected by navigateToCredentials — go straight to BLE panel
    await page.locator("#btn-next").click();
    await expect(page.locator("#panel-ble")).toBeVisible();

    // Inject a stale error into pair-status
    await page.evaluate(() => {
      const el = document.getElementById("pair-status");
      el.className = "status-box status-error";
      el.textContent = "Previous error";
    });

    // Go back then forward again to BLE panel
    await page.locator("#btn-back-ble").click();
    await page.locator("#btn-next").click();
    await expect(page.locator("#panel-ble")).toBeVisible();

    // pair-status should be cleared
    await expect(page.locator("#pair-status")).toHaveClass(/hidden/);
  });
});

// ── Tests: Password visibility reset ─────────────────────────────────────────

test.describe("Password visibility", () => {
  test("password field resets to hidden when navigating back to device panel", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);

    // Show password
    await page.locator("#btn-pwd-toggle").click();
    await expect(page.locator("#password")).toHaveAttribute("type", "text");
    await expect(page.locator("#btn-pwd-toggle")).toHaveAttribute("aria-pressed", "true");

    // Navigate back to device panel
    await page.locator("#btn-back").click();
    await expect(page.locator("#panel-devices")).toBeVisible();

    // Navigate to credentials again
    await navigateToCredentials(page);

    // Password field should be reset to hidden
    await expect(page.locator("#password")).toHaveAttribute("type", "password");
    await expect(page.locator("#btn-pwd-toggle")).toHaveAttribute("aria-pressed", "false");
  });

  test("password toggle updates aria-pressed correctly", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);
    await navigateToCredentials(page);

    // Initial state: hidden
    await expect(page.locator("#btn-pwd-toggle")).toHaveAttribute("aria-pressed", "false");

    // Show
    await page.locator("#btn-pwd-toggle").click();
    await expect(page.locator("#btn-pwd-toggle")).toHaveAttribute("aria-pressed", "true");

    // Hide again
    await page.locator("#btn-pwd-toggle").click();
    await expect(page.locator("#btn-pwd-toggle")).toHaveAttribute("aria-pressed", "false");
  });
});

// ── Tests: WiFi AP pairing flow ───────────────────────────────────────────────

/**
 * Add a mock POST /api/provision/wifi-ap-pair route + optional EventSource mock.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object} [opts]
 * @param {number}  [opts.postStatus]  - HTTP status for the POST (default: 200)
 * @param {string}  [opts.token]       - token returned in POST response
 * @param {string|null} [opts.sseEvent] - "activated" | "wifi_ap_error" | "onerror" | null
 * @param {object}  [opts.activation]  - payload for "activated" SSE event
 */
async function mockWifiApRoute(page, opts = {}) {
  const postStatus = opts.postStatus ?? 200;
  const token = opts.token ?? "wifi-ap-token-xyz";
  const sseEvent = opts.sseEvent ?? "activated";
  const activation = opts.activation ?? {
    token,
    gw_id: "aabbccdd1122",
    local_key: "aabbccddeeff00112233445566778899",
    ip_address: "192.168.1.55",
  };

  // Mock POST /api/provision/wifi-ap-pair
  await page.route(BASE + "/api/provision/wifi-ap-pair", (route) => {
    if (postStatus !== 200) {
      return route.fulfill({ status: postStatus, body: "Error" });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ token, events_url: BASE + "/api/provision/events" }),
    });
  });

  // Mock EventSource — fires the configured event after a short delay
  await page.addInitScript(
    ({ sseEvent, activation }) => {
      window._wifiApSseEvent = sseEvent;
      window._wifiApActivation = activation;
      window.EventSource = class MockWifiApEventSource {
        constructor(url) {
          this.url = url;
          this.readyState = 1;
          this._cbs = {};
          this._onerrorFn = null;
          const self = this;
          // Fire onerror if that variant is requested
          if (window._wifiApSseEvent === "onerror") {
            setTimeout(() => { if (self._onerrorFn) self._onerrorFn(); }, 300);
          }
        }
        addEventListener(evt, cb) {
          if (!this._cbs[evt]) this._cbs[evt] = [];
          this._cbs[evt].push(cb);
          if (evt === window._wifiApSseEvent && window._wifiApActivation) {
            setTimeout(() => {
              cb({ data: JSON.stringify(window._wifiApActivation) });
            }, 300);
          }
        }
        set onerror(fn) { this._onerrorFn = fn; }
        close() { this.readyState = 2; }
      };
    },
    { sseEvent, activation }
  );
}

/**
 * Navigate through: device card click → fill SSID → fill password → click Pair button.
 */
async function pairViaWifiApUi(page, { ssid = "HomeWifi", password = "secret123" } = {}) {
  // Click the device card in the device discovery panel
  await page.locator(".device-card").first().click();
  await page.waitForSelector("#panel-wifi:not(.hidden)");

  // Fill home WiFi credentials — click first to remove iOS-autocomplete readonly guard
  await page.locator("#ssid").click();
  await page.locator("#ssid").fill(ssid);
  if (password) {
    await page.locator("#password").click();
    await page.locator("#password").fill(password);
  }

  // Click Pair button (btn-next, labeled "Pair device →" in WiFi AP mode)
  await page.locator("#btn-next").click();
}

test.describe("WiFi AP pairing flow", () => {
  test("device card appears when quick-scan returns a Tuya AP", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await loadPage(page);

    await expect(page.locator(".device-card")).toHaveCount(1);
    await expect(page.locator(".device-card").first()).toContainText("SmartLife_AB12");
  });

  test("multiple Tuya APs each get their own device card", async ({ page }) => {
    await setupRoutes(page, {
      tuya_aps: [{ ssid: "SmartLife_AA11" }, { ssid: "SmartLife_BB22" }],
    });
    await loadPage(page);

    await expect(page.locator(".device-card")).toHaveCount(2);
    await expect(page.locator(".device-card").nth(0)).toContainText("SmartLife_AA11");
    await expect(page.locator(".device-card").nth(1)).toContainText("SmartLife_BB22");
  });

  test("no Tuya APs — no-devices message is visible", async ({ page }) => {
    await setupRoutes(page);  // tuya_aps defaults to []
    await loadPage(page);

    await expect(page.locator("#no-devices-msg")).toBeVisible();
    await expect(page.locator(".device-card")).toHaveCount(0);
  });

  test("clicking device card navigates to credentials panel", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await loadPage(page);

    await page.locator(".device-card").first().click();

    await expect(page.locator("#panel-devices")).toBeHidden();
    await expect(page.locator("#panel-wifi")).toBeVisible();
  });

  test("credentials panel shows 'Pair device' button label after WiFi AP selection", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await loadPage(page);

    await page.locator(".device-card").first().click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");

    await expect(page.locator("#btn-next-label")).toContainText("Pair device");
  });

  test("successful WiFi AP pairing shows done screen", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page);
    await loadPage(page);

    await pairViaWifiApUi(page);

    await expect(page.locator("#panel-done")).toBeVisible({ timeout: 5000 });
    await expect(page.locator("#panel-wifi")).toBeHidden();
  });

  test("done screen shows device gw_id and local_key after WiFi AP pairing", async ({ page }) => {
    const token = "tok-wifi-ap-1";
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, {
      token,
      activation: {
        token,
        gw_id: "device99aabb",
        local_key: "1122334455667788990011223344556677",
        ip_address: "10.0.0.55",
      },
    });
    await loadPage(page);

    await pairViaWifiApUi(page);
    await page.waitForSelector("#panel-done:not(.hidden)", { timeout: 5000 });

    await expect(page.locator("#panel-done")).toContainText("device99aabb");
    await expect(page.locator("#panel-done")).toContainText("1122334455667788990011223344556677");
  });

  test("SSE activated event with wrong token does not complete WiFi AP pairing", async ({ page }) => {
    // Server sends an "activated" event with a DIFFERENT token — must be ignored.
    // Pairing should time out (SSE_TIMEOUT_MS), not show the done screen.
    const ourToken = "correct-token-abc";
    const wrongToken = "wrong-token-xyz";
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, {
      token: ourToken,
      activation: {
        token: wrongToken,       // ← deliberately wrong token
        gw_id: "baddevice",
        local_key: "aabbccddeeff00112233445566778899",
        ip_address: "10.0.0.99",
      },
    });
    await loadPage(page);
    await pairViaWifiApUi(page);

    // Done screen must NOT appear — the wrong token event should be silently ignored
    await expect(page.locator("#panel-done")).toBeHidden({ timeout: 600 });
    // The pairing UI should still be on the credentials panel
    await expect(page.locator("#panel-wifi")).toBeVisible();
  });

  test("SSE activated event with non-null token in pre-token window is rejected", async ({ page }) => {
    // Regression guard for the pre-token race: before our POST returns (token=null),
    // a concurrent user's activation event should NOT be accepted.
    // Old code: `(!token || d.token == null || d.token === token)` — `!token` was true → accepted wrong events.
    // New code: `(d.token == null || d.token === token)` — null !== "other-token" → rejected.
    const ourToken = "our-session-token";
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });

    // POST responds slowly (simulating slow network / HA under load)
    await page.route(BASE + "/api/provision/wifi-ap-pair", async (route) => {
      await new Promise((r) => setTimeout(r, 400));  // POST delayed 400ms
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ token: ourToken, events_url: BASE + "/api/provision/events" }),
      });
    });

    // SSE fires IMMEDIATELY (0ms) with a non-null foreign token — simulates another user's
    // device activating before our POST response arrives and sets our token variable.
    await page.addInitScript(() => {
      window.EventSource = class PreTokenRaceEventSource {
        constructor() { this.readyState = 1; this._cbs = {}; }
        addEventListener(evt, cb) {
          if (!this._cbs[evt]) this._cbs[evt] = [];
          this._cbs[evt].push(cb);
          if (evt === "activated") {
            // Fire immediately — before the POST response sets token
            setTimeout(() => {
              cb({ data: JSON.stringify({
                token: "other-users-token",  // non-null, won't match ourToken
                gw_id: "foreign-device",
                local_key: "aabbccddeeff00112233445566778899",
              }) });
            }, 10);
          }
        }
        set onerror(_fn) {}
        close() { this.readyState = 2; }
      };
    });

    await loadPage(page);
    await pairViaWifiApUi(page);

    // Foreign token event should be silently discarded — done screen must NOT appear
    await expect(page.locator("#panel-done")).toBeHidden({ timeout: 600 });
    await expect(page.locator("#panel-wifi")).toBeVisible();
  });

  test("'Pair Another Device' button is enabled after second pairing via WiFi AP", async ({ page }) => {
    // Regression: goToDevices() disabled btn-pair-another but showDone() didn't re-enable it.
    // On second pairing, the button would be visible but permanently disabled.
    const token = "tok-pair-another";
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, {
      token,
      activation: { token, gw_id: "dev1", local_key: "aabbccddeeff00112233445566778899" },
    });
    await loadPage(page);

    // First pairing — reach done screen
    await pairViaWifiApUi(page);
    await page.waitForSelector("#panel-done:not(.hidden)", { timeout: 5000 });

    // Click "Pair Another Device" — goes back to device panel
    await page.locator("#btn-pair-another").click();
    await page.waitForSelector("#panel-devices:not(.hidden)");

    // Second pairing
    await pairViaWifiApUi(page);
    await page.waitForSelector("#panel-done:not(.hidden)", { timeout: 5000 });

    // The "Pair Another Device" button must be enabled (not disabled from first round)
    await expect(page.locator("#btn-pair-another")).toBeEnabled();
    await expect(page.locator("#btn-pair-another")).toBeVisible();
  });

  test("POST 500 error shows error status and re-enables pair button", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, { postStatus: 500, sseEvent: null });
    await loadPage(page);

    await pairViaWifiApUi(page);

    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#wifi-ap-status")).toContainText("500");
    await expect(page.locator("#btn-next")).toBeEnabled();
  });

  test("SSE wifi_ap_error event shows error message on credentials panel", async ({ page }) => {
    const token = "tok-err";
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, {
      token,
      sseEvent: "wifi_ap_error",
      activation: { token, error: "connect_failed" },
    });
    await loadPage(page);

    await pairViaWifiApUi(page);

    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#panel-wifi")).toBeVisible();
    await expect(page.locator("#panel-done")).toBeHidden();
  });

  test("SSE connection error (onerror) shows error and re-enables pair button", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, { sseEvent: "onerror" });
    await loadPage(page);

    await pairViaWifiApUi(page);

    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#btn-next")).toBeEnabled({ timeout: 3000 });
  });

  test("Back button from credentials after WiFi AP selection returns to device panel", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await loadPage(page);

    await page.locator(".device-card").first().click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");

    await page.locator("#btn-back").click();

    await expect(page.locator("#panel-devices")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeHidden();
  });

  test("goToDevices clears stale pair-status from previous BLE attempt", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await loadPage(page);

    // Navigate to credentials panel first (btn-back lives here)
    await page.locator(".device-card").first().click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");

    // Inject a stale error into #pair-status (simulates leftover from previous BLE attempt)
    await page.evaluate(() => {
      const el = document.getElementById("pair-status");
      if (el) {
        el.className = "status-box status-error";
        el.textContent = "Stale BLE error from last attempt";
      }
    });

    // Navigate back to device panel — calls goToDevices() which must clear the status
    await page.locator("#btn-back").click();
    await page.waitForSelector("#panel-devices:not(.hidden)");

    // pair-status should be cleared / hidden
    await expect(page.locator("#pair-status")).toHaveClass(/hidden/);
  });

  test("POST 409 conflict shows friendly 'already in progress' message", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, { postStatus: 409, sseEvent: null });
    await loadPage(page);

    await pairViaWifiApUi(page);

    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    // Message should mention "in progress" or be an error (not the raw HTTP status code)
    await expect(page.locator("#wifi-ap-status")).not.toContainText("409");
    await expect(page.locator("#wifi-ap-status")).toContainText("progress");
    await expect(page.locator("#btn-next")).toBeEnabled();
  });

  test("Refresh scan button clears old cards and re-runs autoDetectDevices", async ({ page }) => {
    // Initial scan: empty
    await setupRoutes(page);
    await loadPage(page);
    await expect(page.locator(".device-card")).toHaveCount(0);

    // Now mock quick-scan to return a device
    await page.route(BASE + "/api/provision/quick-scan", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ tuya_aps: [{ ssid: "SmartLife_CC33" }] }),
      })
    );

    // Click refresh
    await page.locator("#btn-refresh-scan").click();
    await page.waitForSelector(".device-card");

    await expect(page.locator(".device-card")).toHaveCount(1);
    await expect(page.locator(".device-card").first()).toContainText("SmartLife_CC33");
  });

  test("refresh button is re-enabled after scan completes", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    // Click refresh and wait for scan to finish (no-devices message appears)
    await page.locator("#btn-refresh-scan").click();
    await page.waitForSelector("#no-devices-msg:not(.hidden)");

    // Button should be re-enabled after scan
    await expect(page.locator("#btn-refresh-scan")).toBeEnabled();
  });

  test("wifi-ap-status is cleared when re-navigating to credentials panel", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, { postStatus: 500, sseEvent: null });
    await loadPage(page);

    // First attempt — trigger an error
    await pairViaWifiApUi(page);
    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#wifi-ap-status")).toHaveClass(/status-error/);

    // Navigate back to device panel
    await page.locator("#btn-back").click();
    await page.waitForSelector("#panel-devices:not(.hidden)");

    // Re-select the device → credentials panel
    await page.locator(".device-card").first().click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");

    // wifi-ap-status should be cleared (not showing the previous error)
    await expect(page.locator("#wifi-ap-status")).toHaveClass(/hidden/);
  });

  test("503 nmcli-unavailable shows friendly error and re-enables pair button", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    // Mock wifi-ap-pair to return 503 (nmcli not installed)
    await page.route(BASE + "/api/provision/wifi-ap-pair", (route) =>
      route.fulfill({
        status: 503,
        contentType: "text/plain",
        body: "nmcli is not available on this host",
      })
    );
    await loadPage(page);

    await pairViaWifiApUi(page);

    // Should show an error (not a generic HTTP status string)
    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#wifi-ap-status")).toHaveClass(/status-error/);
    // Should contain friendly nmcli message (from wifi_ap_nmcli_missing i18n key)
    await expect(page.locator("#wifi-ap-status")).not.toContainText("HTTP 503");
    // Pair button re-enabled so user can retry
    await expect(page.locator("#btn-next")).toBeEnabled();
  });

  test("WiFi scan error closes dropdown instead of showing misleading empty state", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await loadPage(page);

    // Navigate to credentials panel
    await page.locator(".device-card").first().click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");

    // Mock wifi-scan to fail
    await page.route(BASE + "/api/provision/wifi-scan", (route) =>
      route.fulfill({ status: 500, body: "Internal Server Error" })
    );

    await page.locator("#btn-wifi-scan").click();

    // Dropdown should remain hidden on error (not show "No networks found")
    await page.waitForTimeout(500);
    await expect(page.locator("#wifi-dropdown")).toHaveClass(/hidden/);
  });

  test("Cancel button appears during WiFi AP pairing and restores UI on click", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    // Slow SSE — never fires (pairing stays in-progress)
    await mockWifiApRoute(page, { sseEvent: null });
    await loadPage(page);

    await pairViaWifiApUi(page);

    // Cancel button should be visible during pairing
    await expect(page.locator("#btn-cancel-wifi-ap")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#btn-back")).toBeHidden();

    // Click cancel
    await page.locator("#btn-cancel-wifi-ap").click();

    // Cancel button should hide, back button and pair button should reappear
    await expect(page.locator("#btn-cancel-wifi-ap")).toBeHidden();
    await expect(page.locator("#btn-back")).toBeVisible();
    await expect(page.locator("#btn-next")).toBeEnabled();
  });

  test("WiFi scan dropdown capped at 20 SSIDs", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    // Override wifi-scan to return 30 SSIDs
    const manySsids = Array.from({ length: 30 }, (_, i) => "Network" + i);
    await page.route(BASE + "/api/provision/wifi-scan", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ssids: manySsids, tuya_aps: [], current_ssid: null }),
      })
    );
    await loadPage(page);
    await page.locator(".device-card").first().click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");
    await page.locator("#btn-wifi-scan").click();
    await page.waitForSelector("#wifi-dropdown:not(.hidden)");

    const items = await page.locator("#wifi-dropdown .wifi-option").count();
    expect(items).toBeLessThanOrEqual(20);
  });

  test("empty password (open WiFi) is accepted and advances to BLE panel", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [] });
    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("OpenWifi");
    // Leave password empty — open network. BLE already selected by navigateToCredentials.
    await page.locator("#btn-next").click();
    // Should advance to BLE panel without error
    await expect(page.locator("#panel-ble")).toBeVisible();
    await expect(page.locator("#s1-error")).toHaveClass(/hidden/);
  });

  test("single-character SSID is accepted", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [] });
    await loadPage(page);
    await navigateToCredentials(page);
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("A");
    // BLE already selected by navigateToCredentials
    await page.locator("#btn-next").click();
    // Should advance without showing an SSID validation error
    await expect(page.locator("#panel-ble")).toBeVisible();
    await expect(page.locator("#s1-error")).toHaveClass(/hidden/);
  });

  test("Escape key cancels active WiFi AP pairing and restores UI", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    // SSE never fires — pairing stays in-progress so cancel button is visible
    await mockWifiApRoute(page, { sseEvent: null });
    await loadPage(page);

    await pairViaWifiApUi(page);

    // Cancel button should be visible during pairing
    await expect(page.locator("#btn-cancel-wifi-ap")).toBeVisible({ timeout: 3000 });

    // Press Escape — should cancel the pairing
    await page.keyboard.press("Escape");

    // Cancel button hidden, back button and pair button restored
    await expect(page.locator("#btn-cancel-wifi-ap")).toBeHidden();
    await expect(page.locator("#btn-back")).toBeVisible();
    await expect(page.locator("#btn-next")).toBeEnabled();
  });

  test("quick-scan server error falls back to no-devices message without blocking UI", async ({ page }) => {
    await setupRoutes(page);
    // Override quick-scan to return a server error
    await page.route(BASE + "/api/provision/quick-scan", (route) =>
      route.fulfill({ status: 500, body: "Internal Server Error" })
    );
    await loadPage(page);

    // Scanning indicator should clear and no-devices message should appear
    await expect(page.locator("#no-devices-msg")).toBeVisible({ timeout: 3000 });
    // Refresh button should be re-enabled so user can retry
    await expect(page.locator("#btn-refresh-scan")).toBeEnabled();
    // BLE scan button should still be usable
    await expect(page.locator("#btn-ble-scan")).toBeVisible();
  });

  test("quick-scan error updates aria-live region for screen readers", async ({ page }) => {
    await setupRoutes(page);
    // Override quick-scan to return a server error
    await page.route(BASE + "/api/provision/quick-scan", (route) =>
      route.fulfill({ status: 500, body: "Internal Server Error" })
    );
    await loadPage(page);

    // devices-status aria-live region must be populated so screen readers announce the failure
    await expect(page.locator("#no-devices-msg")).toBeVisible({ timeout: 3000 });
    const liveText = await page.locator("#devices-status").textContent();
    expect(liveText.trim().length).toBeGreaterThan(0);
  });

  test("navigating back to device panel after WiFi AP cancel leaves UI in clean state", async ({ page }) => {
    // After cancel + back-to-devices, the credentials panel must be cleared (no stale errors
    // or pairing status) and a second attempt must be possible (pair button re-enabled).
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    await mockWifiApRoute(page, { sseEvent: null });  // SSE never fires — stays in-progress
    await loadPage(page);

    // Start pairing then cancel
    await pairViaWifiApUi(page);
    await expect(page.locator("#btn-cancel-wifi-ap")).toBeVisible({ timeout: 3000 });
    await page.locator("#btn-cancel-wifi-ap").click();

    // Navigate back to devices panel
    await page.locator("#btn-back").click();
    await page.waitForSelector("#panel-devices:not(.hidden)");

    // State must be clean — device panel showing, no stale status
    await expect(page.locator("#panel-devices")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeHidden();

    // Re-clicking the device card should navigate back to credentials with clean state
    await page.locator(".device-card").first().click();
    await page.waitForSelector("#panel-wifi:not(.hidden)");

    // wifi-ap-status must be hidden (cleared by goToCredentials)
    await expect(page.locator("#wifi-ap-status")).toHaveClass(/hidden/);
    // Pair button must be enabled for the new attempt
    await expect(page.locator("#btn-next")).toBeEnabled();
    // Cancel button must be hidden (not showing from previous attempt)
    await expect(page.locator("#btn-cancel-wifi-ap")).toBeHidden();
  });

  test("wifi-ap-status uses role=alert + aria-live=assertive when showing an error", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    // POST fails → setWifiApStatus("status-error", ...) is called
    await mockWifiApRoute(page, { postStatus: 500 });
    await loadPage(page);

    await pairViaWifiApUi(page);

    // Wait for the error to appear
    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#wifi-ap-status")).toHaveClass(/status-error/);

    // Screen readers must receive immediate (assertive) announcement for errors
    const role     = await page.locator("#wifi-ap-status").getAttribute("role");
    const ariaLive = await page.locator("#wifi-ap-status").getAttribute("aria-live");
    expect(role).toBe("alert");
    expect(ariaLive).toBe("assertive");
  });

  test("wifi-ap-status reverts to role=status + aria-live=polite for non-error updates", async ({ page }) => {
    await setupRoutes(page, { tuya_aps: [{ ssid: "SmartLife_AB12" }] });
    // Slow SSE — spinner stays up (info/polite state)
    await mockWifiApRoute(page, { sseEvent: null });
    await loadPage(page);

    await pairViaWifiApUi(page);

    // Spinner is shown (status-info class)
    await expect(page.locator("#wifi-ap-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#wifi-ap-status")).toHaveClass(/status-info/);

    // Non-error: polite live region (no urgency)
    const role     = await page.locator("#wifi-ap-status").getAttribute("role");
    const ariaLive = await page.locator("#wifi-ap-status").getAttribute("aria-live");
    expect(role).toBe("status");
    expect(ariaLive).toBe("polite");
  });
});
