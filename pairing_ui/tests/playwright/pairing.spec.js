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

  // Mock /api/provision/wifi-scan
  await page.route(BASE + "/api/provision/wifi-scan", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ssids }) })
  );

  // Mock QR endpoint
  await page.route(BASE + "/api/provision/qr.svg", (route) =>
    route.fulfill({ status: 503, body: "" })
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
        // CRC-16/MODBUS (simplified — just append 2 zero bytes for mock)
        const frame = new Uint8Array(body.length + 2);
        frame.set(body);
        return frame;
      }

      let _notifyCb = null;
      let _writeCount = 0;

      const fakeChar = {
        startNotifications: async () => {},
        addEventListener: (_evt, cb) => { _notifyCb = cb; },
        writeValueWithoutResponse: async (chunk) => {
          _writeCount++;
          const chunkNo = chunk[0], total = chunk[1];
          const isLastChunk = chunkNo + 1 === total;
          if (!isLastChunk || !_notifyCb) return;

          // Read cmd from first chunk payload (offset 2+3 = header byte 3)
          const data = chunk.slice(2);
          const cmd = data.length > 3 ? data[3] : 0xff;

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
          }, 50);
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

// ── Tests: Step 1 — WiFi form ─────────────────────────────────────────────────

test.describe("Step 1 — WiFi credentials form", () => {
  test("page loads with WiFi panel visible and BLE panel hidden", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    await expect(page.locator("#panel-wifi")).toBeVisible();
    await expect(page.locator("#panel-ble")).toBeHidden();
    await expect(page.locator("#panel-done")).toBeHidden();
  });

  test("step counter shows STEP 1 OF 2", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    const counter = page.locator("#step-counter");
    await expect(counter).toBeVisible();
    await expect(counter).toContainText("1");
    await expect(counter).toContainText("2");
  });

  test("clicking Next without SSID shows error", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    // SSID field is readonly+empty by default — just click Next to trigger validation
    await page.locator("#btn-next").click();

    await expect(page.locator("#s1-error")).toBeVisible();
    await expect(page.locator("#panel-wifi")).toBeVisible();
    await expect(page.locator("#panel-ble")).toBeHidden();
  });

  test("entering SSID and clicking Next advances to step 2", async ({ page }) => {
    await setupRoutes(page);
    await loadPage(page);

    // Trigger focus to remove readonly
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyHomeNetwork");
    await page.locator("#btn-next").click();

    await expect(page.locator("#panel-wifi")).toBeHidden();
    await expect(page.locator("#panel-ble")).toBeVisible();
    const counter = page.locator("#step-counter");
    await expect(counter).toContainText("2");
  });
});

// ── Tests: WiFi scan ──────────────────────────────────────────────────────────

test.describe("WiFi scan dropdown", () => {
  test("scan button fetches networks and shows dropdown", async ({ page }) => {
    await setupRoutes(page, { ssids: ["HomeNet", "GuestNet", "WorkNet"] });
    await loadPage(page);

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

    await page.locator("#btn-wifi-scan").click();
    await page.locator(".wifi-option", { hasText: "OfficeNet" }).click();

    await expect(page.locator("#ssid")).toHaveValue("OfficeNet");
    await expect(page.locator("#wifi-dropdown")).toBeHidden();
  });

  test("empty scan result shows 'No networks found'", async ({ page }) => {
    await setupRoutes(page, { ssids: [] });
    await loadPage(page);

    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator(".wifi-option.muted")).toBeVisible();
    await expect(page.locator(".wifi-option.muted")).toContainText("No networks found");
  });

  test("clicking outside dropdown closes it", async ({ page }) => {
    await setupRoutes(page, { ssids: ["HomeNet"] });
    await loadPage(page);

    await page.locator("#btn-wifi-scan").click();
    await expect(page.locator("#wifi-dropdown")).toBeVisible();

    // Click somewhere outside the dropdown
    await page.locator("footer").click();
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
    // Navigate to step 2 first
    await page.locator("#ssid").click();
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

  test("successful pairing saves SSID to localStorage", async ({ page }) => {
    await setupRoutes(page);
    await mockBle(page, ACTIVATION);
    await loadPage(page);

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
    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("MyNet");
    await page.locator("#btn-next").click();
    await page.locator("#btn-pair").click();

    await expect(page.locator("#pair-status")).toBeVisible({ timeout: 3000 });
    await expect(page.locator("#pair-status")).toHaveClass(/status-error/);
    await expect(page.locator("#btn-pair")).toBeEnabled();
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

    await page.locator("#ssid").click();
    await page.locator("#ssid").fill("TestNet");
    await page.locator("#btn-next").click();

    await expect(page.locator("#panel-ble")).toBeVisible();
    await expect(page.locator("#warn-https")).toBeHidden();
    // btn-pair enabled (assuming bluetooth stub present — no warn-browser either)
    await expect(page.locator("#btn-pair")).toBeEnabled();
  });
});
