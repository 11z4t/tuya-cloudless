/**
 * Unit tests for pairing_ui/app.js — PLAT-810 (browser guard) and PLAT-811 (SSID localStorage)
 * @jest-environment jsdom
 */
"use strict";

// app.js is loaded once; the exported symbols are used across all tests.
const app = require("../../custom_components/tuya_cloudless/pairing_ui/app.js");
const { isSupportedBrowser, hasWebBluetooth, saveLastSsid, loadLastSsid, SSID_TTL_MS, SSID_STORAGE_KEY } = app;

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
