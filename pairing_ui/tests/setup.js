/**
 * Jest setup: stub DOM so that app.js IIFE does not crash when required.
 * The IIFE runs on module load; we provide a universal element stub so that
 * every getElementById / querySelector call returns a safe no-op object.
 */
"use strict";

// Universal DOM stub — returned for any element not in the real document
const domStub = () => ({
  addEventListener: () => {},
  removeEventListener: () => {},
  setAttribute: () => {},
  removeAttribute: () => {},
  getAttribute: () => null,
  classList: { add: () => {}, remove: () => {}, contains: () => false },
  style: {},
  get textContent() { return ""; },
  set textContent(_v) {},
  get innerHTML() { return ""; },
  set innerHTML(_v) {},
  get value() { return ""; },
  set value(_v) {},
  get disabled() { return false; },
  set disabled(_v) {},
  childElementCount: 0,
  appendChild: () => {},
  contains: () => false,
});

// Patch getElementById to return stub for missing elements
const _orig = document.getElementById.bind(document);
document.getElementById = (id) => _orig(id) || domStub();

// Provide a minimal real lang-select so detectLang() / changeLang() work
document.body.innerHTML = `<select id="lang-select"><option value="en">EN</option></select>`;

// Silence fetch — init() tries to loadLang() and loadServerConfig()
global.fetch = jest.fn(() => Promise.reject(new Error("fetch stubbed in test")));

// Suppress unhandled async errors from the IIFE
process.on("unhandledRejection", () => {});
