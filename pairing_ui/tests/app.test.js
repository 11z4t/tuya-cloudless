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
  countUtf8Bytes,
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
