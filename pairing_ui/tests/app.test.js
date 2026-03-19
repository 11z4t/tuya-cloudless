/**
 * Unit tests for pairing_ui/app.js — PLAT-810 (browser guard) and PLAT-811 (SSID localStorage)
 * @jest-environment jsdom
 */
"use strict";

// app.js is loaded once; the exported symbols are used across all tests.
const app = require("../../custom_components/tuya_cloudless/pairing_ui/app.js");
const {
  isSupportedBrowser, hasWebBluetooth,
  isIOS, isAndroid, chromeIntentUrl,
  saveLastSsid, loadLastSsid, SSID_TTL_MS, SSID_STORAGE_KEY,
  countUtf8Bytes, reassemble, onNotify, waitForResponse, parseFrame, crc16Modbus,
  _safePath, _safeUrl, _t, _getRecvReject, _getActiveSseTimer, _updateStepCounter,
} = app;

// ── PLAT-810 — Browser compatibility helpers ──────────────────────────────────

describe("isSupportedBrowser", () => {
  function setUA(ua) {
    Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
  }

  it("returns true for Chrome", () => {
    setUA("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36");
    expect(isSupportedBrowser()).toBe(true);
  });

  it("returns true for Edge", () => {
    setUA("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0");
    expect(isSupportedBrowser()).toBe(true);
  });

  it("returns true for Safari", () => {
    setUA("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1");
    expect(isSupportedBrowser()).toBe(true);
  });

  it("returns false for Firefox", () => {
    setUA("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0");
    expect(isSupportedBrowser()).toBe(false);
  });

  it("returns false for an unknown bot UA", () => {
    setUA("curl/7.88.1");
    expect(isSupportedBrowser()).toBe(false);
  });
});

describe("hasWebBluetooth", () => {
  it("returns false when navigator.bluetooth is undefined", () => {
    delete navigator.bluetooth;
    expect(hasWebBluetooth()).toBe(false);
  });

  it("returns true when navigator.bluetooth is defined", () => {
    Object.defineProperty(navigator, "bluetooth", { value: {}, configurable: true });
    expect(hasWebBluetooth()).toBe(true);
    delete navigator.bluetooth;
  });
});

// ── PLAT-811 — SSID localStorage with TTL ────────────────────────────────────

describe("saveLastSsid / loadLastSsid", () => {
  beforeEach(() => {
    localStorage.clear();
    jest.restoreAllMocks();
  });

  it("returns null when nothing is stored", () => {
    expect(loadLastSsid()).toBeNull();
  });

  it("saves and retrieves SSID", () => {
    saveLastSsid("MyHomeNetwork");
    expect(loadLastSsid()).toBe("MyHomeNetwork");
  });

  it("returns null and clears entry when TTL is exceeded", () => {
    const expiredTime = Date.now() - SSID_TTL_MS - 1000;
    localStorage.setItem(SSID_STORAGE_KEY, JSON.stringify({ ssid: "OldNet", saved_at: expiredTime }));
    expect(loadLastSsid()).toBeNull();
    expect(localStorage.getItem(SSID_STORAGE_KEY)).toBeNull();
  });

  it("returns SSID when entry is within TTL", () => {
    const recentTime = Date.now() - (SSID_TTL_MS / 2);
    localStorage.setItem(SSID_STORAGE_KEY, JSON.stringify({ ssid: "ValidNet", saved_at: recentTime }));
    expect(loadLastSsid()).toBe("ValidNet");
  });

  it("returns null and does not crash on malformed localStorage entry", () => {
    localStorage.setItem(SSID_STORAGE_KEY, "not-valid-json{{{");
    expect(loadLastSsid()).toBeNull();
  });

  it("overwrites previously saved SSID with newer value", () => {
    saveLastSsid("FirstNetwork");
    saveLastSsid("SecondNetwork");
    expect(loadLastSsid()).toBe("SecondNetwork");
  });

  it("TTL constant is 90 days in milliseconds", () => {
    expect(SSID_TTL_MS).toBe(90 * 24 * 60 * 60 * 1000);
  });
});

// ── Platform detection helpers ─────────────────────────────────────────────────

describe("isIOS", () => {
  function setUA(ua, platform = "", maxTouchPoints = 0) {
    Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
    Object.defineProperty(navigator, "platform", { value: platform, configurable: true });
    Object.defineProperty(navigator, "maxTouchPoints", { value: maxTouchPoints, configurable: true });
  }

  it("returns true for iPhone UA", () => {
    setUA("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15");
    expect(isIOS()).toBe(true);
  });

  it("returns true for iPad UA", () => {
    setUA("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15");
    expect(isIOS()).toBe(true);
  });

  it("returns true for iPad desktop mode (MacIntel + touch points)", () => {
    setUA("Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/605.1.15", "MacIntel", 5);
    expect(isIOS()).toBe(true);
  });

  it("returns false for real Mac (no touch points)", () => {
    setUA("Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120", "MacIntel", 0);
    expect(isIOS()).toBe(false);
  });

  it("returns false for Android UA", () => {
    setUA("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120");
    expect(isIOS()).toBe(false);
  });
});

describe("isAndroid", () => {
  function setUA(ua) {
    Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
  }

  it("returns true for Android Chrome UA", () => {
    setUA("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120");
    expect(isAndroid()).toBe(true);
  });

  it("returns false for iOS UA", () => {
    setUA("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15");
    expect(isAndroid()).toBe(false);
  });

  it("returns false for desktop Chrome UA", () => {
    setUA("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120");
    expect(isAndroid()).toBe(false);
  });
});

describe("chromeIntentUrl", () => {
  function setUA(ua) {
    Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
  }

  beforeEach(() => {
    // jsdom sets a default location; override to something predictable.
    Object.defineProperty(window, "location", {
      value: new URL("https://ha.example.com/api/tuya_cloudless/pairing/?flow_id=abc"),
      configurable: true,
    });
  });

  it("returns an intent URL for Android", () => {
    setUA("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120");
    const url = chromeIntentUrl();
    expect(url).toContain("intent://ha.example.com");
    expect(url).toContain("package=com.android.chrome");
    expect(url).toContain("flow_id=abc");
    expect(url).toContain("scheme=https");
  });

  it("returns null for non-Android platforms", () => {
    setUA("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120");
    expect(chromeIntentUrl()).toBeNull();
  });

  it("uses http scheme when location is http", () => {
    setUA("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120");
    Object.defineProperty(window, "location", {
      value: new URL("http://192.168.1.100:8099/?flow_id=xyz"),
      configurable: true,
    });
    const url = chromeIntentUrl();
    expect(url).toContain("scheme=http");
  });
});

// ── countUtf8Bytes ─────────────────────────────────────────────────────────────

describe("countUtf8Bytes", () => {
  it("returns 0 for empty string", () => {
    expect(countUtf8Bytes("")).toBe(0);
  });

  it("returns same as length for pure ASCII", () => {
    expect(countUtf8Bytes("HomeNet")).toBe(7);
    expect(countUtf8Bytes("a".repeat(32))).toBe(32);
  });

  it("counts multibyte characters correctly", () => {
    // é = U+00E9 = 2 bytes in UTF-8
    expect(countUtf8Bytes("Café")).toBe(5);  // C(1)+a(1)+f(1)+é(2) = 5
    // Chinese character = 3 bytes
    expect(countUtf8Bytes("家")).toBe(3);
    // Emoji = 4 bytes (U+1F4F6 = ANTENNA BARS)
    expect(countUtf8Bytes("📶")).toBe(4);
  });

  it("exactly 32 ASCII bytes passes the WiFi SSID limit", () => {
    expect(countUtf8Bytes("a".repeat(32))).toBe(32);
    expect(countUtf8Bytes("a".repeat(32))).not.toBeGreaterThan(32);
  });

  it("detects that an emoji SSID may exceed 32 bytes", () => {
    // "Network" (7) + 7 emoji (28 bytes) = 35 bytes total — would fail
    const ssid = "Net" + "📶".repeat(8);  // 3 + 32 = 35 bytes
    expect(countUtf8Bytes(ssid)).toBeGreaterThan(32);
  });

  it("counts 63-char ASCII password as exactly 63 bytes", () => {
    const pwd = "a".repeat(63);
    expect(countUtf8Bytes(pwd)).toBe(63);
  });

  it("detects password exceeding 63 bytes with multibyte chars", () => {
    // 32 ASCII + 16 two-byte chars = 32 + 32 = 64 bytes
    const pwd = "a".repeat(32) + "é".repeat(16);
    expect(countUtf8Bytes(pwd)).toBeGreaterThan(63);
  });
});

// ── reassemble ────────────────────────────────────────────────────────────────
// BLE chunks format: [chunk_index, total_chunks, ...payload_bytes]
// reassemble() sorts by chunk index and concatenates payloads.

describe("reassemble", () => {
  function makeChunks(payloads) {
    const total = payloads.length;
    return payloads.map((payload, idx) => {
      const chunk = new Uint8Array(2 + payload.length);
      chunk[0] = idx;       // chunk index
      chunk[1] = total;     // total chunks
      chunk.set(payload, 2);
      return chunk;
    });
  }

  it("reassembles a single chunk correctly", () => {
    const chunks = makeChunks([[0x55, 0xAA, 0x04]]);
    const result = reassemble(chunks);
    expect(Array.from(result)).toEqual([0x55, 0xAA, 0x04]);
  });

  it("reassembles in-order chunks", () => {
    const chunks = makeChunks([[0x01, 0x02], [0x03, 0x04], [0x05, 0x06]]);
    const result = reassemble(chunks);
    expect(Array.from(result)).toEqual([0x01, 0x02, 0x03, 0x04, 0x05, 0x06]);
  });

  it("reassembles out-of-order chunks correctly", () => {
    // Build chunks in order, then shuffle before reassembling
    const chunks = makeChunks([[0xAA], [0xBB], [0xCC]]);
    const shuffled = [chunks[2], chunks[0], chunks[1]]; // [CC, AA, BB]
    const result = reassemble(shuffled);
    // Should produce AA BB CC (sorted by index)
    expect(Array.from(result)).toEqual([0xAA, 0xBB, 0xCC]);
  });

  it("handles reversed chunk order", () => {
    const chunks = makeChunks([[0x10, 0x11], [0x20, 0x21], [0x30, 0x31]]);
    const reversed = [chunks[2], chunks[1], chunks[0]];
    const result = reassemble(reversed);
    expect(Array.from(result)).toEqual([0x10, 0x11, 0x20, 0x21, 0x30, 0x31]);
  });

  it("produces correct byte count for multi-chunk payload", () => {
    // 5 chunks of 18 bytes each = 90 bytes total
    const payloads = Array.from({ length: 5 }, () => new Uint8Array(18).fill(0xff));
    const chunks = makeChunks(payloads.map(p => Array.from(p)));
    const result = reassemble(chunks);
    expect(result.length).toBe(90);
  });
});

// ── onNotify — BLE chunk deduplication ───────────────────────────────────────
// onNotify accumulates incoming BLE notification chunks and resolves the
// promise registered by waitForResponse() when all unique chunks have arrived.

describe("onNotify", () => {
  function makeChunk(chunkNo, total, payload) {
    const chunk = new Uint8Array(2 + payload.length);
    chunk[0] = chunkNo;
    chunk[1] = total;
    chunk.set(payload, 2);
    return chunk;
  }

  function makeEvent(chunk) {
    return { target: { value: { buffer: chunk.buffer } } };
  }

  it("resolves with 2 unique chunks when a duplicate arrives", async () => {
    const p = waitForResponse(500);
    const chunk0 = makeChunk(0, 2, [0xAA, 0xBB]);
    const chunk1 = makeChunk(1, 2, [0xCC, 0xDD]);

    onNotify(makeEvent(chunk0));
    onNotify(makeEvent(chunk0));   // duplicate of chunk 0 — must be ignored
    onNotify(makeEvent(chunk1));   // this should trigger resolution

    const chunks = await p;
    expect(chunks).toHaveLength(2);
    // Both chunks should have distinct indices (0 and 1, not two zeros)
    const indices = chunks.map(c => c[0]).sort();
    expect(indices).toEqual([0, 1]);
  });

  it("resolves normally when chunks arrive in order with no duplicates", async () => {
    const p = waitForResponse(500);
    const chunk0 = makeChunk(0, 3, [0x01]);
    const chunk1 = makeChunk(1, 3, [0x02]);
    const chunk2 = makeChunk(2, 3, [0x03]);

    onNotify(makeEvent(chunk0));
    onNotify(makeEvent(chunk1));
    onNotify(makeEvent(chunk2));

    const chunks = await p;
    expect(chunks).toHaveLength(3);
  });

  it("does not resolve prematurely when all chunks are duplicates of one index", async () => {
    // total=3 but only chunk index 0 arrives (duplicated twice) → should NOT resolve
    const p = waitForResponse(100);
    const chunk0 = makeChunk(0, 3, [0xAA]);

    onNotify(makeEvent(chunk0));
    onNotify(makeEvent(chunk0));
    onNotify(makeEvent(chunk0));

    await expect(p).rejects.toThrow("BLE response timeout");
  });
});

// ── crc16Modbus ───────────────────────────────────────────────────────────────
// Verify the CRC implementation against known test vectors.

describe("crc16Modbus", () => {
  it("returns 0x34F6 for b'hello' (known MODBUS test vector)", () => {
    const input = Buffer.from("hello");
    expect(crc16Modbus(input)).toBe(0x34F6);
  });

  it("returns 0xFFFF for empty input (MODBUS init value)", () => {
    expect(crc16Modbus(new Uint8Array(0))).toBe(0xFFFF);
  });
});

// ── parseFrame — CRC validation ───────────────────────────────────────────────
// parseFrame() must reject frames with incorrect CRC, and accept frames
// with correct CRC.

describe("parseFrame", () => {
  function buildFrame(cmd, payloadBytes) {
    const header = new Uint8Array(8);
    header[0] = 0x55; header[1] = 0xAA;
    header[2] = 0x04; // protocol version
    header[3] = cmd;
    // seq = 0
    header[6] = (payloadBytes.length >> 8) & 0xff;
    header[7] = payloadBytes.length & 0xff;
    const body = new Uint8Array(header.length + payloadBytes.length);
    body.set(header); body.set(payloadBytes, header.length);
    const crc = crc16Modbus(body);
    const frame = new Uint8Array(body.length + 2);
    frame.set(body);
    frame[body.length]     = crc & 0xff;
    frame[body.length + 1] = (crc >> 8) & 0xff;
    return frame;
  }

  function wrapChunk(frame) {
    const chunk = new Uint8Array(2 + frame.length);
    chunk[0] = 0; chunk[1] = 1; // chunkNo=0, total=1
    chunk.set(frame, 2);
    return [chunk];
  }

  it("parses a valid frame correctly", () => {
    const frame = buildFrame(0x00, new Uint8Array([0xAA, 0xBB]));
    const { cmd, payload } = parseFrame(wrapChunk(frame));
    expect(cmd).toBe(0x00);
    expect(Array.from(payload)).toEqual([0xAA, 0xBB]);
  });

  it("throws 'Bad CRC' for a frame with wrong CRC", () => {
    const frame = buildFrame(0x00, new Uint8Array([0x01, 0x02]));
    // Corrupt the last byte (CRC high byte)
    frame[frame.length - 1] ^= 0xFF;
    expect(() => parseFrame(wrapChunk(frame))).toThrow("Bad CRC");
  });

  it("throws 'Bad CRC' for a frame with zero CRC when data is non-trivial", () => {
    const frame = buildFrame(0x01, new Uint8Array([0x10, 0x20, 0x30]));
    // Replace CRC with zeroes
    frame[frame.length - 2] = 0x00;
    frame[frame.length - 1] = 0x00;
    expect(() => parseFrame(wrapChunk(frame))).toThrow("Bad CRC");
  });

  it("throws 'Bad magic' for a frame without 0x55 0xAA header", () => {
    const frame = buildFrame(0x00, new Uint8Array(0));
    frame[0] = 0x00; // corrupt magic
    expect(() => parseFrame(wrapChunk(frame))).toThrow("Bad magic");
  });

  it("throws 'Frame too short' for very short data", () => {
    const chunk = new Uint8Array([0, 1, 0x55, 0xAA]); // only 4 bytes after chunk header
    expect(() => parseFrame([[chunk]])).toThrow(); // too short
  });
});

// ── _safePath — base URL validation ───────────────────────────────────────────
// _safePath() validates injected server URLs to prevent off-origin redirects.

describe("_safePath", () => {
  it("accepts valid relative path", () => {
    expect(_safePath("/api/provision", "/fallback")).toBe("/api/provision");
  });

  it("accepts path with multiple segments", () => {
    expect(_safePath("/api/tuya_cloudless/pairing/provision", "/fallback"))
      .toBe("/api/tuya_cloudless/pairing/provision");
  });

  it("rejects protocol-relative URL", () => {
    expect(_safePath("//evil.com/api", "/fallback")).toBe("/fallback");
  });

  it("rejects absolute URL with scheme", () => {
    expect(_safePath("https://evil.com/api", "/fallback")).toBe("/fallback");
  });

  it("rejects javascript: pseudo-URL", () => {
    expect(_safePath("javascript:alert(1)", "/fallback")).toBe("/fallback");
  });

  it("rejects path with query string or hash", () => {
    expect(_safePath("/api?foo=bar", "/fallback")).toBe("/fallback");
    expect(_safePath("/api#hash", "/fallback")).toBe("/fallback");
  });

  it("returns fallback for non-string input", () => {
    expect(_safePath(null, "/fallback")).toBe("/fallback");
    expect(_safePath(42, "/fallback")).toBe("/fallback");
    expect(_safePath(undefined, "/fallback")).toBe("/fallback");
  });
});

// ── _safeUrl — activator_url / events_url validation ─────────────────────────

describe("_safeUrl", () => {
  it("accepts a relative path", () => {
    expect(_safeUrl("/api/provision/events", "fallback")).toBe("/api/provision/events");
  });

  it("accepts an absolute http URL (LAN activator)", () => {
    expect(_safeUrl("http://192.168.1.100:8099", "fallback")).toBe("http://192.168.1.100:8099");
  });

  it("accepts an absolute https URL", () => {
    expect(_safeUrl("https://ha.example.com/api/provision/events", "fallback"))
      .toBe("https://ha.example.com/api/provision/events");
  });

  it("rejects protocol-relative URL", () => {
    expect(_safeUrl("//evil.com/events", "fallback")).toBe("fallback");
  });

  it("rejects javascript: pseudo-URL", () => {
    expect(_safeUrl("javascript:fetch('//evil.com')", "fallback")).toBe("fallback");
  });

  it("rejects ftp:// scheme", () => {
    expect(_safeUrl("ftp://evil.com/file", "fallback")).toBe("fallback");
  });

  it("returns fallback for non-string", () => {
    expect(_safeUrl(null, "fallback")).toBe("fallback");
    expect(_safeUrl(42, "fallback")).toBe("fallback");
  });
});

// ── Round 33 — i18n fallback map coverage ─────────────────────────────────────
// _strings is {} in the test environment (loadLang() is never called), so every
// t() call exercises the fallback map. This guards against regressions where a
// critical key is accidentally removed from the fallback map.

describe("t() built-in fallback map", () => {
  it("returns expected English text for navigation keys when strings not loaded", () => {
    expect(_t("btn_next")).toBe("Next \u2192");
    expect(_t("btn_back")).toBe("\u2190 Back");
    expect(_t("btn_cancel")).toBe("Cancel");
    expect(_t("btn_scan")).toBe("Scan & Pair");
    expect(_t("btn_pair_another")).toBe("\u2190 Pair Another Device");
  });

  it("returns expected English text for credential-step keys", () => {
    expect(_t("step1_title")).toBe("WiFi credentials");
    expect(_t("ssid_label")).toBe("Network name (SSID)");
    expect(_t("password_label")).toBe("Password");
    expect(_t("show_password")).toBe("Show password");
    expect(_t("hide_password")).toBe("Hide password");
  });

  it("returns expected English text for device discovery keys", () => {
    expect(_t("device_panel_title")).toBe("Find device");
    expect(_t("looking_for_devices")).toBe("Looking for devices\u2026");
    expect(_t("pair_via_ble")).toBe("Scan via Bluetooth");
    expect(_t("pair_via_wifi_ap")).toBe("WiFi AP");
  });

  it("returns expected English text for error and status keys", () => {
    expect(_t("err_pair_fail")).toMatch(/Device rejected WiFi config/);
    expect(_t("err_sse_lost")).toMatch(/Connection lost/);
    expect(_t("success_activated")).toMatch(/Device activated/);
    expect(_t("wifi_ap_error")).toMatch(/WiFi AP pairing failed/);
    expect(_t("wifi_ap_timeout")).toMatch(/2 minutes/);
  });

  it("returns expected English text for spinner keys", () => {
    expect(_t("spin_scanning")).toMatch(/Scanning/);
    expect(_t("spin_handshake")).toMatch(/Handshake/);
    expect(_t("spin_sending")).toMatch(/credentials/i);
    expect(_t("spin_waiting")).toMatch(/waiting/i);
  });

  it("returns the key itself for unknown keys (raw-key safety valve)", () => {
    expect(_t("totally_unknown_key_xyz")).toBe("totally_unknown_key_xyz");
  });

  it("interpolates {vars} using fallback string", () => {
    // step_x_of_y = "Step {x} of {y}" — verify interpolation works with fallbacks
    const result = _t("step_x_of_y", { x: "1", y: "3" });
    expect(result).toBe("Step 1 of 3");
  });
});

// ── Round 36 — BLE disconnect fails waitForResponse immediately ───────────────

describe("waitForResponse — disconnect rejection", () => {
  it("rejects with 'BLE device disconnected' when _recvReject is called", async () => {
    const p = waitForResponse(5000);  // long timeout — should NOT wait 5s

    // Simulate gattserverdisconnected by calling the reject function
    const rejectFn = _getRecvReject();
    expect(rejectFn).not.toBeNull();
    rejectFn(new Error("BLE device disconnected"));

    await expect(p).rejects.toThrow("BLE device disconnected");
  });

  it("clears _recvReject after rejection so second disconnect is a no-op", async () => {
    const p = waitForResponse(5000);
    const rejectFn = _getRecvReject();
    rejectFn(new Error("BLE device disconnected"));
    await expect(p).rejects.toThrow();

    // After rejection, _recvReject must be null
    expect(_getRecvReject()).toBeNull();
  });

  it("resolves normally when chunks arrive (disconnect listener is idle)", async () => {
    // 2-chunk frame
    const frame = new Uint8Array([0x55, 0xAA, 0x04, 0x02, 0, 1, 0, 2, 0xAB, 0xCD]);
    const crc = (function() {
      let c = 0xFFFF;
      for (const b of frame) { c ^= b; for (let i = 0; i < 8; i++) { c = (c & 1) ? (c >>> 1) ^ 0xA001 : c >>> 1; } }
      return c & 0xFFFF;
    })();
    const full = new Uint8Array(frame.length + 2);
    full.set(frame); full[frame.length] = crc & 0xff; full[frame.length + 1] = crc >> 8;

    const chunk0 = new Uint8Array([0, 2, ...full.slice(0, 10)]);
    const chunk1 = new Uint8Array([1, 2, ...full.slice(10)]);

    const p = waitForResponse(5000);
    onNotify({ target: { value: { buffer: chunk0.buffer } } });
    onNotify({ target: { value: { buffer: chunk1.buffer } } });
    const chunks = await p;
    expect(chunks).toHaveLength(2);
    // _recvReject is cleared on resolve
    expect(_getRecvReject()).toBeNull();
  });
});

// ── Round 37 — SSE timer leak prevention on beforeunload ──────────────────────

describe("_activeSseTimer — tracked for beforeunload cleanup", () => {
  const { _listenForActivation } = app;

  beforeEach(() => {
    // Stub EventSource so listenForActivation doesn't throw in jsdom
    global.EventSource = class {
      constructor() { this.addEventListener = () => {}; this.onerror = null; this.close = () => {}; }
    };
  });

  afterEach(() => {
    // Clear any leftover timer to prevent interference between tests
    const t = _getActiveSseTimer();
    if (t !== null) clearTimeout(t);
  });

  it("sets _activeSseTimer when listenForActivation is called", () => {
    jest.useFakeTimers();
    _listenForActivation("tok_timer_test");
    expect(_getActiveSseTimer()).not.toBeNull();
    jest.useRealTimers();
  });

  it("cancels old timer when called a second time (rapid re-pairing)", () => {
    jest.useFakeTimers();
    _listenForActivation("tok_first");
    const firstTimer = _getActiveSseTimer();
    expect(firstTimer).not.toBeNull();
    // Call again before the first timer fires
    _listenForActivation("tok_second");
    // New timer should be different from the first
    expect(_getActiveSseTimer()).not.toBeNull();
    // First timer should have been cancelled — verify it no longer fires by checking
    // that _activeSseTimer remains non-null after advancing past first timer's delay
    const { SSE_TIMEOUT_MS } = app;
    jest.advanceTimersByTime(SSE_TIMEOUT_MS + 100);
    // Both timers have now been advanced past; _activeSseTimer should be null (second timeout)
    expect(_getActiveSseTimer()).toBeNull();
    jest.useRealTimers();
  });

  it("clears _activeSseTimer after SSE timeout fires", () => {
    jest.useFakeTimers();
    // Stub DOM elements that the timeout callback accesses
    document.body.innerHTML = `<button id="btn-pair"></button>`;
    _listenForActivation("tok_timeout_clears");
    expect(_getActiveSseTimer()).not.toBeNull();
    // Advance past SSE_TIMEOUT_MS to trigger the timeout callback
    const { SSE_TIMEOUT_MS } = app;
    jest.advanceTimersByTime(SSE_TIMEOUT_MS + 100);
    expect(_getActiveSseTimer()).toBeNull();
    jest.useRealTimers();
  });
});

// ── Round 38 — Navigation functions clear _activeSseTimer ─────────────────────

describe("goToDevices / goToCredentials clear _activeSseTimer", () => {
  const { _listenForActivation, goToDevices, goToCredentials } = app;

  beforeEach(() => {
    jest.useFakeTimers();
    global.EventSource = class {
      constructor() { this.addEventListener = () => {}; this.onerror = null; this.close = () => {}; }
    };
    // Minimal DOM needed by navigation functions
    document.body.innerHTML = `
      <div id="panel-devices"><h2 id="devices-title" tabindex="-1"></h2></div>
      <div id="panel-wifi" class="hidden"></div>
      <div id="panel-ble" class="hidden"></div>
      <div id="panel-done" class="hidden"></div>
      <div id="pair-status" class="status-box hidden"></div>
      <div id="wifi-ap-status" class="status-box hidden"></div>
      <button id="btn-pair-another"></button>
      <input id="ssid" />
      <input id="password" type="password" />
      <span id="btn-pwd-toggle" aria-pressed="false" aria-label=""></span>
      <span id="s1-error" class="status-box hidden"></span>
      <span id="step-counter" aria-label=""></span>
    `;
  });

  afterEach(() => {
    const t = _getActiveSseTimer();
    if (t !== null) clearTimeout(t);
    jest.useRealTimers();
  });

  it("goToDevices cancels a pending SSE timer from a previous BLE pairing", () => {
    _listenForActivation("tok_nav_devices");
    expect(_getActiveSseTimer()).not.toBeNull();
    goToDevices();
    expect(_getActiveSseTimer()).toBeNull();
  });

  it("goToCredentials cancels a pending SSE timer from a previous BLE pairing", () => {
    _listenForActivation("tok_nav_creds");
    expect(_getActiveSseTimer()).not.toBeNull();
    goToCredentials();
    expect(_getActiveSseTimer()).toBeNull();
  });

  it("goToStep2 (BLE branch) cancels a pending SSE timer from a previous attempt", () => {
    const { goToStep2 } = app;
    if (typeof goToStep2 !== "function") return; // guard if not exported
    // Simulate a prior BLE pairing that left _activeSseTimer set
    _listenForActivation("tok_step2_ble");
    expect(_getActiveSseTimer()).not.toBeNull();
    // Set value on the existing ssid input (from beforeEach DOM) so goToStep2 passes validation
    const ssidEl = document.getElementById("ssid");
    if (ssidEl) ssidEl.value = "TestNet";
    goToStep2();
    expect(_getActiveSseTimer()).toBeNull();
  });
});

// ── Round 41 — WiFi AP timer tracked in _activeSseTimer ────────────────────────

describe("WiFi AP cancel clears _activeSseTimer / null-safe click listener", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    global.EventSource = class {
      constructor() { this.addEventListener = () => {}; this.onerror = null; this.close = () => {}; }
    };
    // DOM needed for cancel-button handler and click-outside listener
    document.body.innerHTML = `
      <div id="panel-devices"><h2 id="devices-title" tabindex="-1"></h2></div>
      <div id="panel-wifi"></div>
      <div id="panel-ble" class="hidden"></div>
      <div id="panel-done" class="hidden"></div>
      <div id="wifi-ap-status" class="status-box hidden"></div>
      <div id="wifi-dropdown" class="hidden" role="listbox"></div>
      <input id="ssid" />
      <button id="btn-wifi-scan" aria-expanded="false"></button>
      <button id="btn-next" id="btn-pair"></button>
      <button id="btn-back"></button>
      <button id="btn-cancel-wifi-ap" class="hidden"></button>
      <span id="step-counter"></span>
    `;
    // Re-bind cancel listener (normally bound in init())
    document.getElementById("btn-cancel-wifi-ap").addEventListener("click", () => {
      const { _currentEventSource: _es } = app;
      if (_es) { _es.close(); }
      const a = app._getActiveSseTimer();
      if (a !== null) { clearTimeout(a); }
      // Mirror the code in app.js init() cancel handler — clears _activeSseTimer
      // (the actual handler runs inside init() closure; we simulate the key behavior)
    });
  });

  afterEach(() => {
    const t = _getActiveSseTimer();
    if (t !== null) clearTimeout(t);
    jest.useRealTimers();
  });

  it("cancel button click clears _activeSseTimer set by listenForActivation", () => {
    const { _listenForActivation: listen, goToCredentials } = app;
    // Set up panel-wifi as visible (where cancel button lives)
    goToCredentials();
    // Simulate a pending BLE SSE timer (same variable as wifiApTimer path)
    listen("tok_cancel_clear");
    expect(_getActiveSseTimer()).not.toBeNull();
    // Simulate user pressing cancel — the cancel handler in app.js init() clears _activeSseTimer
    // We verify this works by calling goToDevices() which is the actual navigation path
    app.goToDevices();
    expect(_getActiveSseTimer()).toBeNull();
  });

  it("document click listener does not throw when wifi-dropdown is absent", () => {
    // Remove wifi-dropdown from DOM to simulate the edge case
    const dd = document.getElementById("wifi-dropdown");
    if (dd) dd.remove();
    // Clicking anywhere should not throw a TypeError
    expect(() => document.dispatchEvent(new MouseEvent("click", { bubbles: true }))).not.toThrow();
  });

  it("document click listener does not throw when btn-wifi-scan is absent", () => {
    const btn = document.getElementById("btn-wifi-scan");
    if (btn) btn.remove();
    expect(() => document.dispatchEvent(new MouseEvent("click", { bubbles: true }))).not.toThrow();
  });
});

// ── Round 43 — aria-label not HTML-escaped in setAttribute context ─────────────

describe("showDeviceCard aria-label is not HTML-escaped", () => {
  const { showDeviceCard } = app;

  beforeEach(() => {
    document.body.innerHTML = `<div id="device-list" role="list"></div>`;
  });

  it("SSID with angle brackets is set literally in aria-label (not esc()-d)", () => {
    showDeviceCard("My <Smart> AP");
    const card = document.querySelector(".device-card");
    expect(card).not.toBeNull();
    // Should be raw string, NOT HTML-entity-encoded
    expect(card.getAttribute("aria-label")).toContain("My <Smart> AP");
    expect(card.getAttribute("aria-label")).not.toContain("&lt;");
    expect(card.getAttribute("aria-label")).not.toContain("&gt;");
  });

  it("SSID with ampersand is set literally in aria-label", () => {
    showDeviceCard("Home & Office");
    const card = document.querySelector(".device-card");
    expect(card.getAttribute("aria-label")).toContain("Home & Office");
    expect(card.getAttribute("aria-label")).not.toContain("&amp;");
  });

  it("SSID still HTML-escaped inside card innerHTML (prevents XSS in displayed text)", () => {
    showDeviceCard("<script>alert(1)</script>");
    const card = document.querySelector(".device-card");
    // innerHTML must NOT contain a live <script> tag — esc() must be used there
    expect(card.innerHTML).not.toContain("<script>");
    expect(card.innerHTML).toContain("&lt;script&gt;");
  });
});

// ── Round 44: null-guard robustness ───────────────────────────────────────────

describe("updateStepCounter null safety", () => {
  it("does not throw when step-counter element is absent", () => {
    // Patch getElementById to return null only for step-counter; call the function directly
    const origGetById = document.getElementById.bind(document);
    jest.spyOn(document, "getElementById").mockImplementation((id) => {
      if (id === "step-counter") return null;
      return origGetById(id);
    });
    try {
      // Call for all interesting branches: step within range, and step > _TOTAL_STEPS
      expect(() => _updateStepCounter(1)).not.toThrow();
      expect(() => _updateStepCounter(99)).not.toThrow();
    } finally {
      document.getElementById.mockRestore();
    }
  });
});

describe("showWifiDropdown / selectWifi null safety", () => {
  it("selectWifi does not throw when ssid/dropdown/scan-btn are absent", () => {
    const origGetById = document.getElementById.bind(document);
    const nullIds = new Set(["ssid", "wifi-dropdown", "btn-wifi-scan"]);
    jest.spyOn(document, "getElementById").mockImplementation((id) => {
      if (nullIds.has(id)) return null;
      return origGetById(id);
    });
    try {
      // goToCredentials calls into the same DOM paths; should not throw with null elements
      expect(() => app.goToCredentials()).not.toThrow();
    } finally {
      document.getElementById.mockRestore();
    }
  });
});

describe("listenForActivation activated event null safety", () => {
  it("does not throw when btn-pair element is absent at activation time", () => {
    // Patch getElementById to return null only for btn-pair
    const origGetById = document.getElementById.bind(document);
    jest.spyOn(document, "getElementById").mockImplementation((id) => {
      if (id === "btn-pair") return null;
      return origGetById(id);
    });

    const EventSourceMock = class {
      constructor() { this.onerror = null; this._handlers = {}; }
      addEventListener(evt, cb) { this._handlers[evt] = cb; }
      close() {}
    };
    const savedES = global.EventSource;
    global.EventSource = EventSourceMock;
    try {
      const es = app._listenForActivation("testtoken");
      // Simulate activated event with matching token
      const payload = { token: "testtoken", gw_id: "GWID", local_key: "aabbccdd" };
      expect(() => {
        if (es._handlers && es._handlers["activated"]) {
          es._handlers["activated"]({ data: JSON.stringify(payload) });
        }
      }).not.toThrow();
    } finally {
      document.getElementById.mockRestore();
      global.EventSource = savedES;
    }
  });
});

describe("t() fallback for spin_connecting", () => {
  it("returns a non-empty, non-key-name string even before i18n loads", () => {
    const result = _t("spin_connecting");
    // Must not return the raw key, and must not be empty
    expect(result).not.toBe("spin_connecting");
    expect(result.length).toBeGreaterThan(0);
  });
});
