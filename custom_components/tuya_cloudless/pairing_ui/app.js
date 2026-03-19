"use strict";

// ── Constants ────────────────────────────────────────────────────────────────
const BLE_SERVICE     = "0000fd50-0000-1000-8000-00805f9b34fb";
const WRITE_CHAR      = "00000001-0000-1001-8001-00805f9b07d0";
const NOTIFY_CHAR     = "00000002-0000-1001-8001-00805f9b07d0";
const BLE_MAX_MTU     = 20;
const PROTOCOL_VER    = 0x04;
const CMD_HANDSHAKE   = 0x00;
const CMD_WIFI_CONFIG = 0x01;
const CMD_PAIR_FAIL   = 0x05;
const WIKI_URL        = "https://github.com/11z4t/tuya-cloudless/wiki/HTTPS-Setup";
const SUPPORTED_LANGS = ["en","sv","de","fr","es","nl","no","da","fi","pl","it","pt","cs","ru","tr","zh","ja","ko","uk","kl","se"];

// ── Browser compatibility helpers (PLAT-810) ──────────────────────────────────
function isSupportedBrowser() {
  const ua = navigator.userAgent;
  return /Chrome\//.test(ua) || /Edg\//.test(ua) || /Safari\//.test(ua);
}
function hasWebBluetooth() {
  return typeof navigator.bluetooth !== "undefined";
}
function isIOS() {
  if (typeof window._TEST_IS_IOS !== "undefined") return window._TEST_IS_IOS;
  return /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}
function isAndroid() {
  if (typeof window._TEST_IS_ANDROID !== "undefined") return window._TEST_IS_ANDROID;
  return /Android/.test(navigator.userAgent);
}
// Build an Android intent URL that opens the current page in Chrome.
// Returns null on non-Android platforms.
function chromeIntentUrl() {
  if (!isAndroid()) return null;
  const url = new URL(window.location.href);
  const host = url.host;
  const path = url.pathname + url.search;
  const scheme = url.protocol === "https:" ? "https" : "http";
  return `intent://${host}${path}#Intent;scheme=${scheme};package=com.android.chrome;end`;
}

// ── Debug log ─────────────────────────────────────────────────────────────────
const _DEBUG_LOG_MAX = 200;  // cap DOM entries to avoid unbounded growth

function dbg(msg) {
  const ts = new Date().toLocaleTimeString();
  const el = document.getElementById("debug-log");
  if (el) {
    // Prune oldest entry when cap is reached
    if (el.childElementCount >= _DEBUG_LOG_MAX) {
      el.removeChild(el.firstChild);
    }
    const line = document.createElement("div");
    line.textContent = "[" + ts + "] " + msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
    // Update count on summary so user knows entries are there
    const summary = document.getElementById("debug-summary-label");
    if (summary) summary.textContent = t("debug_title") + " (" + el.childElementCount + ")";
  }
}

// ── i18n ─────────────────────────────────────────────────────────────────────
let _strings = {};
let _currentLang = "en";

async function loadLang(lang) {
  try {
    const r = await fetch(_STATIC_BASE + "/i18n/" + lang + ".json");
    if (!r.ok) throw new Error("not found");
    _strings = await r.json();
    _currentLang = lang;
    dbg("Language: " + lang + " \u2713");
  } catch (_) {
    if (lang !== "en") {
      dbg("Language " + lang + " not found, falling back to en");
      await loadLang("en");
    }
  }
}

function t(key, vars) {
  let s = _strings[key];
  if (s === undefined) {
    // Provide built-in fallbacks for critical keys so UI never shows raw key names.
    // These mirror en.json so the UI is fully usable even if the i18n fetch fails.
    const fallbacks = {
      // Navigation & chrome
      title:              "Tuya Cloudless \u2014 Pair Device",
      btn_next:           "Next \u2192",
      btn_back:           "\u2190 Back",
      btn_pair_another:   "\u2190 Pair Another Device",
      btn_cancel:         "Cancel",
      btn_scan:           "Scan & Pair",
      btn_add_ha:         "Add to Home Assistant",
      btn_pair_wifi_ap:   "Pair device \u2192",
      step_x_of_y:        "Step {x} of {y}",
      lang_picker_label:  "Language",
      footer_github:      "GitHub",
      footer_no_cloud:    "No cloud account required",
      // Device discovery panel
      device_panel_title:    "Find device",
      device_panel_desc:     "Devices found nearby appear automatically. Or use Bluetooth to scan.",
      looking_for_devices:   "Looking for devices\u2026",
      no_devices_auto_found: "No Tuya devices found nearby. Use Bluetooth below, or put the device in pairing mode and try again.",
      scan_refresh:          "Refresh",
      pair_via_ble:          "Scan via Bluetooth",
      pair_via_wifi_ap:      "WiFi AP",
      // Credential step
      step1_title:       "WiFi credentials",
      step1_desc:        "Your device will connect to this network after pairing.",
      step1_desc_wifiap: "Your device will connect to this network. Enter the password and click Pair.",
      ssid_label:        "Network name (SSID)",
      ssid_placeholder:  "My Home WiFi",
      password_label:    "Password",           // pragma: allowlist secret
      password_placeholder: "WiFi password",   // pragma: allowlist secret
      show_password:     "Show password",       // pragma: allowlist secret
      hide_password:     "Hide password",       // pragma: allowlist secret
      // BLE step
      step2_title:  "Pair device via Bluetooth",
      step2_badge:  "Chrome / Edge",
      step2_desc:   "Make sure your device is flashing rapidly (pairing mode).",
      step2_hint:   "Put your Tuya device in pairing mode (factory reset or hold the pair button until the indicator flashes rapidly), then click Scan & Pair.",
      // Done step
      step3_title:    "Device activated \u2713",
      step3_ha_flow:  "Home Assistant is setting up your device. You can close this window and continue in the Home Assistant UI.",
      label_device_id:  "Device ID",
      label_ip:         "IP address",
      label_local_key:  "Local key",
      label_reveal_key: "Reveal",
      label_hide_key:   "Hide",
      // Status & spinners
      spin_scanning:    "Scanning for Tuya device\u2026",
      spin_connecting:  "Connecting to {name}\u2026",
      spin_handshake:   "Handshake\u2026",
      spin_sending:   "Sending WiFi credentials\u2026",
      spin_waiting:   "Credentials sent \u2713 \u2014 waiting for device to activate\u2026",
      success_activated: "\u2713 Device activated! Local key generated.",
      // WiFi AP status
      wifi_ap_connecting: "Connecting to device AP\u2026",
      wifi_ap_waiting:    "Credentials sent \u2014 waiting for device to join your network\u2026",
      wifi_ap_timeout:    "Device did not activate within 2 minutes. Check pairing mode and try again.",
      wifi_ap_error:      "WiFi AP pairing failed. Check that the device is in pairing mode and try again.",
      wifi_ap_in_progress: "Pairing already in progress \u2014 wait a moment and retry.",
      wifi_ap_nmcli_missing: "WiFi control unavailable \u2014 nmcli is not installed on this Home Assistant host.",
      // Warnings & errors
      warn_no_ssid:           "\u26A0 Enter a WiFi network name first.",
      warn_invalid_ssid:      "\u26A0 Invalid network name (max 32 bytes, no null characters).",
      warn_invalid_password:  "\u26A0 Password too long (max 63 bytes for WPA2).",
      warn_scan_cancelled:    "\u26A0 Bluetooth scan cancelled.",
      warn_ble_disabled:      "\u26A0 Bluetooth is disabled. Enable Bluetooth and try again.",
      warn_no_device_selected:"No device selected. Go back and select a device.",
      err_sse_lost:   "Connection lost \u2014 please try again.",
      err_pair_fail:  "Device rejected WiFi config. Check credentials and retry.",
      // QR panel
      qr_title:       "Open on Android Chrome",
      qr_body:        "Web Bluetooth requires Chrome or Edge. Scan the QR code with your Android phone to open this page there.",
      qr_unavail:     "QR image unavailable. Install: pip install 'tuya-cloudless[qr]'",
      qr_copy_label:  "Or copy this URL and open on Chrome / Edge:",
      qr_copy_btn:    "Copy",
      qr_copy_done:   "Copied!",
      // Error screens
      error_https_required_title: "HTTPS required",
      error_https_required_body:  "Web Bluetooth requires HTTPS. This pairing page is opened via your Home Assistant, which must be configured with HTTPS.",
      error_https_link:           "HTTPS setup guide",
      error_browser_title:         "Browser not supported",
      error_browser_body:          "Web Bluetooth requires Chrome or Edge on desktop or Android.",
      error_browser_unsupported_title: "Unsupported browser",
      error_browser_unsupported_body:  "Web Bluetooth requires Chrome or Edge (desktop or Android).",
      // Misc
      wifi_scan_btn:     "Scan for WiFi networks",
      wifi_scan_none:    "No networks found",
      wifi_current_network: "Current network",
      debug_title:       "Debug log",
      help_text:         "Need help?",
      help_link_label:   "Open guide \u2192",
    };
    s = fallbacks[key] !== undefined ? fallbacks[key] : key;
  }
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      // Escape interpolated values so translated strings cannot be XSS vectors
      s = s.replaceAll("{" + k + "}", esc(String(v)));
    }
  }
  return s;
}

function applyStrings() {
  const el = (id) => document.getElementById(id);
  document.title = t("title");
  el("step1-title").textContent    = t("step1_title");
  el("step1-desc").textContent     = t("step1_desc");
  el("ssid-label").textContent     = t("ssid_label");
  el("ssid").placeholder           = t("ssid_placeholder");
  el("password-label").textContent = t("password_label");
  el("password").placeholder       = t("password_placeholder");
  el("btn-next-label").textContent = t("btn_next");
  el("step2-title").textContent    = t("step2_title");
  el("step2-badge").textContent    = t("step2_badge");
  el("step2-desc").textContent     = t("step2_desc");
  el("step2-hint").textContent     = t("step2_hint");
  el("btn-pair-label").textContent = t("btn_scan");
  el("step3-title").textContent    = t("step3_title");
  el("ha-flow-msg").textContent    = t("step3_ha_flow");
  el("btn-add-ha-label").textContent = t("btn_add_ha");
  el("footer-github").textContent  = t("footer_github");
  el("footer-no-cloud").textContent = t("footer_no_cloud");
  el("qr-title").textContent       = t("qr_title");
  el("qr-body").textContent        = t("qr_body");
  el("qr-unavail").textContent     = t("qr_unavail");
  el("qr-copy-label").textContent  = t("qr_copy_label");
  el("btn-copy").textContent       = t("qr_copy_btn");
  el("err-https-title").textContent  = t("error_https_required_title");
  el("err-https-body").textContent   = t("error_https_required_body");
  el("err-https-link").textContent   = t("error_https_link") + " \u2192";
  el("err-browser-title").textContent = t("error_browser_title");
  el("err-browser-body").textContent  = t("error_browser_body");
  el("err-browser-unsupported-title").textContent = t("error_browser_unsupported_title");
  el("err-browser-unsupported-body").textContent  = t("error_browser_unsupported_body");
  el("help-text-1").textContent    = t("help_text");
  el("help-link-1").textContent    = t("help_link_label");
  el("help-text-2").textContent    = t("help_text");
  el("help-link-2").textContent    = t("help_link_label");
  el("lang-select").setAttribute("aria-label", t("lang_picker_label"));
  if (el("devices-title"))         el("devices-title").textContent         = t("device_panel_title");
  if (el("devices-desc"))          el("devices-desc").textContent          = t("device_panel_desc");
  if (el("devices-scanning-label")) el("devices-scanning-label").textContent = t("looking_for_devices");
  if (el("btn-ble-scan-label"))    el("btn-ble-scan-label").textContent    = t("pair_via_ble");
  if (el("btn-refresh-scan-label")) el("btn-refresh-scan-label").textContent = t("scan_refresh") || "Refresh";
  if (el("btn-back-label"))        el("btn-back-label").textContent        = t("btn_back");
  if (el("btn-back-ble-label"))    el("btn-back-ble-label").textContent    = t("btn_back");
  if (el("btn-pair-another-label")) el("btn-pair-another-label").textContent = t("btn_pair_another");
  if (el("btn-cancel-wifi-ap-label")) el("btn-cancel-wifi-ap-label").textContent = t("btn_cancel") || "Cancel";
  // WiFi scan button aria-label — not set in HTML to avoid duplication; applyStrings owns it.
  if (el("btn-wifi-scan")) el("btn-wifi-scan").setAttribute("aria-label", t("wifi_scan_btn"));
  // Re-apply btn-pwd-toggle aria-label in the current show/hide state
  const pwdToggle = document.getElementById("btn-pwd-toggle");
  if (pwdToggle) {
    const isShowing = document.getElementById("password")?.type === "text";
    pwdToggle.setAttribute("aria-label", isShowing ? t("hide_password") : t("show_password"));
  }
  // Re-apply btn-reveal-key text and aria-label in the current revealed/hidden state
  const revealBtn = document.getElementById("btn-reveal-key");
  if (revealBtn) {
    const isRevealed = document.getElementById("key-revealed")?.style.display !== "none";
    const revealLabel = isRevealed ? t("label_hide_key") : t("label_reveal_key");
    revealBtn.textContent = revealLabel;
    revealBtn.setAttribute("aria-label", revealLabel + " " + t("label_local_key"));
  }
  // Re-apply step counter with new language
  updateStepCounter(_currentStep);
  // Re-apply debug summary count
  const logEl = document.getElementById("debug-log");
  const summary = document.getElementById("debug-summary-label");
  if (summary && logEl) {
    const count = logEl.childElementCount;
    summary.textContent = count > 0
      ? t("debug_title") + " (" + count + ")"
      : t("debug_title");
  }
}

function changeLang(lang) {
  localStorage.setItem("tc-lang", lang);
  loadLang(lang).then(() => {
    applyStrings();
    // Announce language change to screen readers via the hidden live region
    const announcer = document.getElementById("lang-announce");
    if (announcer) {
      announcer.textContent = t("lang_picker_label") + ": " + lang.toUpperCase();
    }
  });
  document.documentElement.lang = lang;
}

function detectLang() {
  const stored = localStorage.getItem("tc-lang");
  if (stored && SUPPORTED_LANGS.includes(stored)) return stored;
  const nav = (navigator.language || "en").substring(0, 2).toLowerCase();
  return SUPPORTED_LANGS.includes(nav) ? nav : "en";
}

// ── Pair method constants — use these instead of bare strings to prevent typos ──
const PAIR_METHOD = Object.freeze({ BLE: "ble", WIFI_AP: "wifi_ap" });

// ── SSE timeout (must match server activation window) ──────────────────────────
const SSE_TIMEOUT_MS = 120_000;

// ── Pair method state ─────────────────────────────────────────────────────────
let _pairMethod = null;       // PAIR_METHOD.BLE | PAIR_METHOD.WIFI_AP
let _selectedApSsid = null;   // SSID of Tuya AP chosen by user
let _currentEventSource = null; // Active EventSource — closed on panel switch
let _activeSseTimer    = null; // setTimeout handle from listenForActivation — cleared on unload

// ── Step counter ──────────────────────────────────────────────────────────────
let _currentStep = 1;
const _TOTAL_STEPS = 3;

function updateStepCounter(step) {
  _currentStep = step;
  const el = document.getElementById("step-counter");
  // Guard: element may be absent in test environments or during early init
  if (!el) return;
  if (step > _TOTAL_STEPS) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.textContent = t("step_x_of_y", { x: step, y: _TOTAL_STEPS }).toUpperCase();
}

// ── SSID persistence (PLAT-811) ───────────────────────────────────────────────
const SSID_STORAGE_KEY = "tuya_cloudless_last_ssid";
const SSID_TTL_MS = 90 * 24 * 60 * 60 * 1000; // 90 days

function saveLastSsid(ssid) {
  try {
    localStorage.setItem(SSID_STORAGE_KEY, JSON.stringify({ ssid, saved_at: Date.now() }));
  } catch (err) {
    dbg("SSID persistence failed (storage full?): " + err.message);
  }
}

function loadLastSsid() {
  try {
    const raw = localStorage.getItem(SSID_STORAGE_KEY);
    if (!raw) return null;
    const { ssid, saved_at } = JSON.parse(raw);
    if (Date.now() - saved_at > SSID_TTL_MS) {
      try { localStorage.removeItem(SSID_STORAGE_KEY); } catch (e) { dbg("SSID cache clear failed: " + e.message); }
      return null;
    }
    return ssid;
  } catch (_) { return null; }
}

// ── Configurable base paths ───────────────────────────────────────────────────
// When served via HA HTTPS these are injected as window globals by the server.
// When accessed directly at http://ha-host:8099/ the globals are undefined and
// the defaults resolve correctly against the page origin.
//
// Validation: only accept relative paths that start with "/" and contain safe
// URL-path characters. Rejects protocol-relative URLs (//evil.com), javascript:
// pseudo-URLs, and anything that could redirect API calls off-origin.
function _safePath(raw, fallback) {
  if (typeof raw !== "string") return fallback;
  return /^\/[a-zA-Z0-9/_-]*$/.test(raw) ? raw : fallback;
}
// Accept relative paths OR absolute http/https URLs (used for activator_url / events_url
// which may be either a LAN HTTP URL or a relative HA HTTPS path).
function _safeUrl(raw, fallback) {
  if (typeof raw !== "string") return fallback;
  if (/^\/[a-zA-Z0-9/_-]*$/.test(raw)) return raw;              // relative path
  if (/^https?:\/\/[a-zA-Z0-9._:/%@-]+$/.test(raw)) return raw; // absolute http(s)
  return fallback;
}
const _PROVISION_BASE = _safePath(
  typeof window._TUYA_PROVISION_BASE !== "undefined" ? window._TUYA_PROVISION_BASE : undefined,
  "/api/provision"
);
const _STATIC_BASE = _safePath(
  typeof window._TUYA_STATIC_BASE !== "undefined" ? window._TUYA_STATIC_BASE : undefined,
  "/static"
);

// ── Server config ─────────────────────────────────────────────────────────────
let ACTIVATOR_URL = (typeof window._TUYA_ACTIVATOR_BASE !== "undefined")
  ? window._TUYA_ACTIVATOR_BASE : window.location.origin;
let EVENTS_URL    = _PROVISION_BASE + "/events";

const _urlParams = new URLSearchParams(window.location.search);
const _haFlowId  = _urlParams.get("flow_id") || null;

async function loadServerConfig() {
  try {
    const r = await fetch(_PROVISION_BASE + "/config");
    if (!r.ok) {
      dbg("WARNING: Server config unavailable (HTTP " + r.status + ") — using defaults");
      return;
    }
    const cfg = await r.json();
    if (cfg.activator_url) ACTIVATOR_URL = _safeUrl(cfg.activator_url, ACTIVATOR_URL);
    if (cfg.events_url)    EVENTS_URL    = _safeUrl(cfg.events_url,    EVENTS_URL);
    dbg("Server: " + ACTIVATOR_URL + " \u2713");
    if (cfg.default_ssid) {
      // Server-provided SSID takes highest priority (HA knows the active network)
      const ssidEl = document.getElementById("ssid");
      if (ssidEl && !ssidEl.value) {
        ssidEl.removeAttribute("readonly"); // readonly trick — field already has value
        ssidEl.value = cfg.default_ssid;
        dbg("Auto-filled SSID: " + cfg.default_ssid + " \u2713");
      }
    } else {
      // Fall back to last successfully paired SSID from localStorage (PLAT-811)
      const lastSsid = loadLastSsid();
      if (lastSsid) {
        const ssidEl = document.getElementById("ssid");
        if (ssidEl && !ssidEl.value) {
          ssidEl.removeAttribute("readonly");
          ssidEl.value = lastSsid;
          dbg("Restored SSID from storage: " + lastSsid + " \u2713");
        }
      }
    }
  } catch (_) { /* keep defaults */ }
}

// ── WiFi scan ─────────────────────────────────────────────────────────────────
async function scanWifi() {
  const btn = document.getElementById("btn-wifi-scan");
  if (!btn) return;
  btn.disabled = true;
  const origIcon = btn.textContent;
  btn.textContent = "\u29D6";  // ⧖ hourglass — visual scanning indicator
  btn.setAttribute("aria-label", t("spin_scanning") || "Scanning…");
  dbg("WiFi scan started\u2026");
  // Close dropdown if already open
  const _ddInit = document.getElementById("wifi-dropdown");
  if (_ddInit) _ddInit.classList.add("hidden");
  try {
    const r = await fetch(_PROVISION_BASE + "/wifi-scan");
    if (!r.ok) throw new Error("scan HTTP " + r.status);
    const data = await r.json();
    // Filter Tuya provisioning APs out of the home-network dropdown — they are
    // not connectable home networks and selecting one would silently break pairing.
    const tuyaApSsids = new Set((data.tuya_aps || []).map(ap => ap.ssid));
    // Filter out empty strings (defensive against malformed server responses) and Tuya APs
    const allSsids = (data.ssids || []).filter(s => s && !tuyaApSsids.has(s));
    // Cap at 20 items — prevents DOM bloat on networks with 50+ APs visible
    const ssids = allSsids.slice(0, 20);
    const current = data.current_ssid || null;
    dbg("WiFi scan: " + ssids.length + "/" + allSsids.length + " home networks shown (" + tuyaApSsids.size + " Tuya APs filtered)");
    showWifiDropdown(ssids, current);
  } catch (err) {
    dbg("WiFi scan error: " + err.message);
    // Close dropdown on error — showing "No networks found" would be misleading
    // when the actual cause is a network or server error, not an empty scan result.
    const dd = document.getElementById("wifi-dropdown");
    if (dd) { dd.classList.add("hidden"); }
    const scanBtnEl = document.getElementById("btn-wifi-scan");
    if (scanBtnEl) scanBtnEl.setAttribute("aria-expanded", "false");
  } finally {
    btn.disabled = false;
    btn.textContent = origIcon;
    // Restore localized label — must match what applyStrings() sets so language switches work.
    btn.setAttribute("aria-label", t("wifi_scan_btn"));
  }
}

function showWifiDropdown(ssids, currentSsid) {
  const dd = document.getElementById("wifi-dropdown");
  if (!dd) return;
  dd.innerHTML = "";
  dd.setAttribute("role", "listbox");
  dd.setAttribute("aria-label", t("ssid_label"));
  if (ssids.length === 0) {
    const item = document.createElement("div");
    item.className = "wifi-option muted";
    item.setAttribute("role", "option");
    item.setAttribute("aria-disabled", "true");
    item.textContent = t("wifi_scan_none");
    dd.appendChild(item);
  } else {
    for (const ssid of ssids) {
      const item = document.createElement("div");
      item.className = "wifi-option";
      item.setAttribute("role", "option");
      item.setAttribute("tabindex", "0");
      item.setAttribute("aria-selected", "false");  // updated by moveFocus() on navigation
      item.textContent = ssid;
      if (ssid === currentSsid) {
        item.className += " wifi-option-current";
        item.setAttribute("aria-selected", "true");
        item.title = t("wifi_current_network") || "Current network";
      }
      const pick = () => selectWifi(ssid);
      item.addEventListener("click", pick);
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
        // Helper: move focus + update aria-selected so screen readers announce the new item
        const moveFocus = (target) => {
          if (!target) return;
          dd.querySelectorAll("[role='option']").forEach(o => o.setAttribute("aria-selected", "false"));
          target.setAttribute("aria-selected", "true");
          target.focus();
        };
        if (e.key === "ArrowDown") {
          e.preventDefault();
          moveFocus(item.nextElementSibling || dd.firstElementChild);
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          moveFocus(item.previousElementSibling || dd.lastElementChild);
        }
        if (e.key === "PageDown") {
          e.preventDefault();
          const items = Array.from(dd.querySelectorAll("[tabindex='0']"));
          const idx = items.indexOf(item);
          moveFocus(items[Math.min(idx + 5, items.length - 1)]);
        }
        if (e.key === "PageUp") {
          e.preventDefault();
          const items = Array.from(dd.querySelectorAll("[tabindex='0']"));
          const idx = items.indexOf(item);
          moveFocus(items[Math.max(idx - 5, 0)]);
        }
        if (e.key === "Escape") {
          dd.classList.add("hidden");
          const sb = document.getElementById("btn-wifi-scan");
          if (sb) sb.setAttribute("aria-expanded", "false");
          const si = document.getElementById("ssid");
          if (si) si.focus();
        }
        if (e.key === "Tab") {
          // Close dropdown on Tab so keyboard users can move on without tabbing
          // through every option.  Do NOT preventDefault — let the browser move
          // focus naturally to the next element in the tab sequence.
          dd.classList.add("hidden");
          const sb = document.getElementById("btn-wifi-scan");
          if (sb) sb.setAttribute("aria-expanded", "false");
        }
      });
      dd.appendChild(item);
    }
  }
  dd.classList.remove("hidden");
  const scanBtnExpand = document.getElementById("btn-wifi-scan");
  if (scanBtnExpand) scanBtnExpand.setAttribute("aria-expanded", "true");
  // Move focus into the first item for keyboard users
  const first = dd.querySelector("[tabindex='0']");
  if (first) first.focus();
}

function selectWifi(ssid) {
  const ssidEl = document.getElementById("ssid");
  if (ssidEl) ssidEl.value = ssid;
  const ddEl = document.getElementById("wifi-dropdown");
  if (ddEl) ddEl.classList.add("hidden");
  const scanBtnEl = document.getElementById("btn-wifi-scan");
  if (scanBtnEl) scanBtnEl.setAttribute("aria-expanded", "false");
  // Return focus to the SSID field so keyboard users can continue to the
  // password field via Tab — without this, focus lands on the now-hidden
  // dropdown item and screen reader context is lost.
  if (ssidEl) ssidEl.focus();
}

// ── Device discovery ───────────────────────────────────────────────────────────
function showDeviceCard(ssid) {
  const list = document.getElementById("device-list");
  // Wrap in role="listitem" so screen readers announce items within the role="list"
  const item = document.createElement("div");
  item.setAttribute("role", "listitem");
  const card = document.createElement("button");
  card.type = "button";
  card.className = "device-card";
  card.dataset.ssid = ssid;
  // Do NOT use esc() here: setAttribute sets the literal attribute value; the browser
  // handles any special characters. Using esc() would expose HTML entities (&lt;, &amp;)
  // verbatim in the accessible name announced by screen readers.
  card.setAttribute("aria-label", ssid + " — " + t("pair_via_wifi_ap"));
  card.innerHTML =
    "<div class=\"device-card-left\">" +
    "<div class=\"device-card-name\">" + esc(ssid) + "</div>" +
    "<span class=\"device-ap-badge\">" + esc(t("pair_via_wifi_ap")) + "</span>" +
    "</div>" +
    "<span class=\"device-card-arrow\" aria-hidden=\"true\">\u203a</span>";
  card.addEventListener("click", () => selectDeviceWifiAp(ssid));
  item.appendChild(card);
  list.appendChild(item);
}

let _scanInProgress = false;  // guard against concurrent autoDetectDevices() calls

async function autoDetectDevices() {
  if (_scanInProgress) return;
  _scanInProgress = true;

  const scanEl   = document.getElementById("devices-scanning");
  const noDevEl  = document.getElementById("no-devices-msg");
  const refreshBtn = document.getElementById("btn-refresh-scan");
  if (refreshBtn) refreshBtn.disabled = true;

  // Clear any previously rendered device cards so a refresh starts clean.
  // #devices-scanning is now a sibling of #device-list (moved outside the list
  // so the list only contains role=listitem children), so all list children are cards.
  const list = document.getElementById("device-list");
  if (list) {
    Array.from(list.children).forEach(child => list.removeChild(child));
  }
  if (noDevEl) noDevEl.className = "status-box status-info hidden";
  if (scanEl) scanEl.style.display = "";  // show scanning indicator

  try {
    // Abort after 5 s — quick-scan reads OS WiFi cache, must be fast.
    // A hung server should not leave the page stuck on "Looking for devices…".
    const scanAbort = new AbortController();
    const scanAbortTimer = setTimeout(() => scanAbort.abort(), 5000);
    let r;
    try {
      r = await fetch(_PROVISION_BASE + "/quick-scan", { signal: scanAbort.signal });
    } finally {
      clearTimeout(scanAbortTimer);
    }
    if (!r.ok) throw new Error("scan HTTP " + r.status);
    const data = await r.json();
    const aps = data.tuya_aps || [];
    if (scanEl) scanEl.style.display = "none";
    const statusLive = document.getElementById("devices-status");
    if (aps.length === 0) {
      if (noDevEl) {
        noDevEl.textContent = t("no_devices_auto_found");
        noDevEl.className = "status-box status-info";
      }
      if (statusLive) statusLive.textContent = t("no_devices_auto_found");
    } else {
      for (const ap of aps) { if (ap.ssid) showDeviceCard(ap.ssid); }
      dbg("Found " + aps.length + " Tuya AP(s)");
      if (statusLive) statusLive.textContent = aps.length + " " + t("pair_via_wifi_ap");
    }
  } catch (_) {
    if (scanEl) scanEl.style.display = "none";
    if (noDevEl) {
      noDevEl.textContent = t("no_devices_auto_found");
      noDevEl.className = "status-box status-info";
    }
    // Update aria-live region so screen readers announce the scan failure
    const statusLive = document.getElementById("devices-status");
    if (statusLive) statusLive.textContent = t("no_devices_auto_found");
  } finally {
    _scanInProgress = false;
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function selectDeviceWifiAp(ssid) {
  _pairMethod = PAIR_METHOD.WIFI_AP;
  _selectedApSsid = ssid;
  dbg("Selected WiFi AP device: " + ssid);
  goToCredentials();
  // Update button label and step hint for WiFi AP flow
  const btnLabel = document.getElementById("btn-next-label");
  if (btnLabel) btnLabel.textContent = t("btn_pair_wifi_ap") || "Pair device →";
  const desc = document.getElementById("step1-desc");
  if (desc) desc.textContent = t("step1_desc_wifiap") || t("step1_desc");
}

function selectDeviceBle() {
  _pairMethod = PAIR_METHOD.BLE;
  _selectedApSsid = null;
  dbg("Selected BLE pairing");
  goToCredentials();
  // Restore BLE button label
  const btnLabel = document.getElementById("btn-next-label");
  if (btnLabel) btnLabel.textContent = t("btn_next");
  const desc = document.getElementById("step1-desc");
  if (desc) desc.textContent = t("step1_desc");
}

// Close dropdown when clicking outside
document.addEventListener("click", (e) => {
  const dd = document.getElementById("wifi-dropdown");
  const scanBtn = document.getElementById("btn-wifi-scan");
  // Guard: elements may be absent in test environments or during late-load
  if (!dd || !scanBtn) return;
  const ssidInput = document.getElementById("ssid");
  if (!dd.contains(e.target) && e.target !== ssidInput && e.target !== scanBtn) {
    dd.classList.add("hidden");
    scanBtn.setAttribute("aria-expanded", "false");
  }
});

// ── Step navigation ───────────────────────────────────────────────────────────
let _ssid = "";
let _pwd  = "";

function goToDevices() {
  // Disable "Pair Another" to prevent double-click triggering duplicate navigation
  const pairAnotherBtn = document.getElementById("btn-pair-another");
  if (pairAnotherBtn) pairAnotherBtn.disabled = true;
  // Close any open SSE connection when navigating back to device discovery
  if (_currentEventSource) {
    _currentEventSource.close();
    _currentEventSource = null;
  }
  // Also cancel any pending SSE timeout that could fire on the new panel
  if (_activeSseTimer !== null) { clearTimeout(_activeSseTimer); _activeSseTimer = null; }
  _pairMethod = null;
  _selectedApSsid = null;
  // Clear form state so credentials from a previous pairing cannot be reused accidentally
  _ssid = "";
  _pwd  = "";
  const ssidEl = document.getElementById("ssid");
  const pwdEl  = document.getElementById("password");
  if (ssidEl) ssidEl.value = "";
  if (pwdEl) {
    pwdEl.value = "";
    // Reset password visibility to hidden so it's not exposed on the next visit
    pwdEl.type = "password";
    const toggle = document.getElementById("btn-pwd-toggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", "false");
      toggle.setAttribute("aria-label", t("show_password"));
    }
  }
  document.getElementById("panel-wifi").classList.add("hidden");
  document.getElementById("panel-ble").classList.add("hidden");
  document.getElementById("panel-done").classList.add("hidden");
  document.getElementById("panel-devices").classList.remove("hidden");
  // Clear pairing status from any previous BLE or WiFi AP attempt
  const pairStatus = document.getElementById("pair-status");
  if (pairStatus) pairStatus.className = "status-box hidden";
  updateStepCounter(1);
  dbg("Step 1: Device discovery");
  // Move focus to panel heading for screen reader announcement
  const title = document.getElementById("devices-title");
  if (title) { title.setAttribute("tabindex", "-1"); title.focus(); }
  // Auto-refresh device scan — previously-paired devices have joined home WiFi
  // and are no longer in AP mode.  Running quick-scan clears stale cards.
  // The _scanInProgress guard prevents duplicate calls if scan is already running.
  autoDetectDevices();
}

function goToCredentials() {
  // Defensive: close any stale SSE from a previous pairing that wasn't already cleaned up.
  if (_currentEventSource) {
    _currentEventSource.close();
    _currentEventSource = null;
  }
  if (_activeSseTimer !== null) { clearTimeout(_activeSseTimer); _activeSseTimer = null; }
  document.getElementById("panel-devices").classList.add("hidden");
  document.getElementById("panel-ble").classList.add("hidden");
  document.getElementById("panel-wifi").classList.remove("hidden");
  updateStepCounter(2);
  dbg("Step 2: WiFi credentials");
  // Ensure any open WiFi dropdown is closed (may be open if user scanned then navigated away)
  const dd = document.getElementById("wifi-dropdown");
  if (dd) { dd.classList.add("hidden"); }
  const scanBtn = document.getElementById("btn-wifi-scan");
  if (scanBtn) scanBtn.setAttribute("aria-expanded", "false");
  // Clear stale error state from a previous visit to this panel
  const errEl = document.getElementById("s1-error");
  if (errEl) { errEl.className = "status-box hidden"; errEl.removeAttribute("tabindex"); }
  // Clear stale WiFi AP status from a previous pairing attempt
  const apStatus = document.getElementById("wifi-ap-status");
  if (apStatus) apStatus.className = "status-box hidden";
  // Ensure cancel button is hidden and back button is visible on fresh credentials entry
  const cancelBtnG = document.getElementById("btn-cancel-wifi-ap");
  const backBtnG   = document.getElementById("btn-back");
  if (cancelBtnG) cancelBtnG.classList.add("hidden");
  if (backBtnG)   backBtnG.classList.remove("hidden");
  // Reset password field to hidden — ensures it's not left visible after BLE back navigation
  const pwdCredEl = document.getElementById("password");
  if (pwdCredEl && pwdCredEl.type === "text") {
    pwdCredEl.type = "password";
    const toggle = document.getElementById("btn-pwd-toggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", "false");
      toggle.setAttribute("aria-label", t("show_password"));
    }
  }
  // Move focus to SSID input so user can start typing immediately
  const ssidEl = document.getElementById("ssid");
  if (ssidEl) ssidEl.focus();
}

// Called by btn-next — branches on _pairMethod
function goToStep2() {
  _ssid = document.getElementById("ssid").value.trim();
  const errEl = document.getElementById("s1-error");
  if (!_ssid) {
    errEl.className = "status-box status-warn";
    errEl.textContent = t("warn_no_ssid");
    errEl.setAttribute("tabindex", "-1");
    errEl.focus();
    return;
  }
  // WiFi spec: SSID max 32 bytes (UTF-8). Check byte count, not JS .length (code units).
  if (_ssid.includes("\x00") || countUtf8Bytes(_ssid) > 32) {
    errEl.className = "status-box status-warn";
    errEl.textContent = t("warn_invalid_ssid");
    errEl.setAttribute("tabindex", "-1");
    errEl.focus();
    return;
  }
  errEl.className = "status-box hidden";
  _pwd = document.getElementById("password").value;

  // WPA2 PSK: password max 63 bytes (WiFi Alliance spec). Empty is OK (open network).
  if (_pwd && countUtf8Bytes(_pwd) > 63) {
    errEl.className = "status-box status-warn";
    errEl.textContent = t("warn_invalid_password");
    errEl.setAttribute("tabindex", "-1");
    errEl.focus();
    return;
  }

  if (_pairMethod === PAIR_METHOD.WIFI_AP) {
    // Guard: should not happen, but protect against manual navigation without device selection
    if (!_selectedApSsid) {
      errEl.className = "status-box status-warn";
      errEl.textContent = t("warn_no_device_selected") || "No device selected. Go back and select a device.";
      errEl.setAttribute("tabindex", "-1");
      errEl.focus();
      return;
    }
    // WiFi AP: pair inline — stay on panel-wifi, show status below the button
    startPairing();
    return;
  }

  // BLE: close any stale SSE connection from a previous WiFi AP attempt, then navigate
  if (_currentEventSource) { _currentEventSource.close(); _currentEventSource = null; }
  if (_activeSseTimer !== null) { clearTimeout(_activeSseTimer); _activeSseTimer = null; }
  // Clear any status message from a previous BLE pairing attempt
  const pairStatusEl = document.getElementById("pair-status");
  if (pairStatusEl) pairStatusEl.className = "status-box hidden";
  const bleBackBtn = document.getElementById("btn-back-ble");
  if (bleBackBtn) bleBackBtn.disabled = false;
  document.getElementById("panel-wifi").classList.add("hidden");
  document.getElementById("panel-ble").classList.remove("hidden");
  updateStepCounter(3);
  dbg("Step 3: BLE pairing");
}

function showDone(gw_id, local_key, ip_address) {
  // Defensive: coerce null/undefined to empty string so esc() doesn't render "null"
  gw_id      = (gw_id      != null) ? String(gw_id)      : "";
  local_key  = (local_key  != null) ? String(local_key)  : "";
  ip_address = (ip_address != null) ? String(ip_address) : "";
  // Clear sensitive credential from memory now that pairing is complete
  _pwd = "";
  document.getElementById("panel-ble").classList.add("hidden");
  document.getElementById("panel-wifi").classList.add("hidden");
  document.getElementById("panel-devices").classList.add("hidden");
  document.getElementById("panel-done").classList.remove("hidden");
  updateStepCounter(_TOTAL_STEPS + 1);  // hides counter

  // Render as a <dl> for semantic structure (screen readers announce label/value pairs)
  const grid = document.getElementById("result-grid");
  grid.setAttribute("aria-label", t("step3_title"));
  grid.innerHTML =
    "<dt class=\"result-label\">" + esc(t("label_device_id")) + "</dt>" +
    "<dd class=\"result-value\">" + esc(gw_id) + "</dd>" +
    "<dt class=\"result-label\">" + esc(t("label_ip")) + "</dt>" +
    "<dd class=\"result-value\">" + esc(ip_address) + "</dd>" +
    "<dt class=\"result-label\">" + esc(t("label_local_key")) + "</dt>" +
    "<dd class=\"result-value key-value\" style=\"display:flex;align-items:center;gap:8px\">" +
    "<span id=\"key-masked\">\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (hidden)</span>" +
    "<span id=\"key-revealed\" style=\"display:none;word-break:break-all\">" + esc(local_key) + "</span>" +
    "<button type=\"button\" id=\"btn-reveal-key\" class=\"btn-sm\" " +
    "aria-label=\"" + esc(t("label_reveal_key")) + " " + esc(t("label_local_key")) + "\" " +
    "style=\"padding:2px 8px;font-size:0.75rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;cursor:pointer\">" +
    esc(t("label_reveal_key")) + "</button>" +
    "</dd>";

  if (_haFlowId) {
    document.getElementById("ha-flow-msg").classList.remove("hidden");
  } else {
    // Validate params before building deep-link to avoid garbage values
    const safeGwId = /^[a-zA-Z0-9_-]{1,64}$/.test(gw_id) ? gw_id : "";
    // Accept 16-char keys (some device types) or 32-char keys; reject anything else
    const safeKey  = /^[a-f0-9]{16,32}$/i.test(local_key) ? local_key : "";
    // Accept IPv4 or IPv6 (link-local, global) — let HA validate the exact format
    const safeIp   = /^[a-f0-9:.\[\]%]{2,45}$/i.test(ip_address) ? ip_address : "";
    if (safeGwId && safeKey) {
      const params = new URLSearchParams({ domain: "tuya_cloudless", gw_id: safeGwId, local_key: safeKey });
      if (safeIp) params.set("ip_address", safeIp);
      document.getElementById("btn-add-ha").href = "/config/integrations/add?" + params;
      document.getElementById("btn-add-ha").classList.remove("hidden");
    }
  }
  const pairAnotherBtn = document.getElementById("btn-pair-another");
  if (pairAnotherBtn) { pairAnotherBtn.disabled = false; pairAnotherBtn.classList.remove("hidden"); }

  // Move focus to the done title so screen readers announce the success state
  const doneTitle = document.getElementById("step3-title");
  if (doneTitle) { doneTitle.setAttribute("tabindex", "-1"); doneTitle.focus(); }

  // Wire up the key reveal toggle
  const revealBtn = document.getElementById("btn-reveal-key");
  if (revealBtn) {
    revealBtn.addEventListener("click", function() {
      const masked   = document.getElementById("key-masked");
      const revealed = document.getElementById("key-revealed");
      const showing  = revealed && revealed.style.display !== "none";
      if (masked)   masked.style.display   = showing ? "" : "none";
      if (revealed) revealed.style.display = showing ? "none" : "";
      const newLabel = showing ? t("label_reveal_key") : t("label_hide_key");
      this.textContent = newLabel;
      this.setAttribute("aria-label", newLabel + " " + t("label_local_key"));
    }, { once: false });
  }
}

// ── CRC-16/MODBUS ─────────────────────────────────────────────────────────────
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

// ── Frame encoding ────────────────────────────────────────────────────────────
function encodeFrame(cmd, seq, payload) {
  const header = new Uint8Array(8);
  header[0] = 0x55; header[1] = 0xAA;
  header[2] = PROTOCOL_VER;
  header[3] = cmd;
  header[4] = (seq >> 8) & 0xFF; header[5] = seq & 0xFF;
  header[6] = (payload.length >> 8) & 0xFF; header[7] = payload.length & 0xFF;

  const body = new Uint8Array(header.length + payload.length);
  body.set(header);
  body.set(payload, header.length);

  const crc = crc16Modbus(body);
  const frame = new Uint8Array(body.length + 2);
  frame.set(body);
  frame[body.length]     = crc & 0xFF;
  frame[body.length + 1] = (crc >> 8) & 0xFF;
  return frame;
}

// ── Chunking ──────────────────────────────────────────────────────────────────
function chunkFrame(frameBytes) {
  const PAYLOAD = BLE_MAX_MTU - 2;
  const chunks = [];
  let offset = 0;
  while (offset < frameBytes.length) {
    chunks.push(frameBytes.slice(offset, offset + PAYLOAD));
    offset += PAYLOAD;
  }
  const total = chunks.length;
  return chunks.map((data, i) => {
    const chunk = new Uint8Array(2 + data.length);
    chunk[0] = i; chunk[1] = total;
    chunk.set(data, 2);
    return chunk;
  });
}

// ── Response parsing ──────────────────────────────────────────────────────────
let _recvChunks = [];
let _recvResolve = null;
let _recvReject  = null;  // called on BLE disconnect to fail waitForResponse immediately

function onNotify(event) {
  const data = new Uint8Array(event.target.value.buffer);
  const chunkNo = data[0];
  const total   = data[1];
  // Drop duplicate chunk indices — BLE may redeliver on link-layer noise.
  // Without this guard, a duplicate chunk would cause premature resolve with
  // length === total but missing one of the original indices.
  if (_recvChunks.some(c => c[0] === chunkNo)) return;
  _recvChunks.push(data);
  // Trigger when all expected chunks have arrived, regardless of arrival order.
  // reassemble() sorts by chunk index so out-of-order delivery is handled.
  if (_recvChunks.length === total && _recvResolve) {
    const resolve = _recvResolve;
    _recvResolve = null;
    resolve([..._recvChunks]);
    _recvChunks = [];
  }
}

function waitForResponse(timeoutMs) {
  if (timeoutMs === undefined) timeoutMs = 10000;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      _recvResolve = null;
      _recvReject  = null;
      _recvChunks = [];  // clear stale chunks so retries start fresh
      reject(new Error("BLE response timeout"));
    }, timeoutMs);
    _recvResolve = (chunks) => { clearTimeout(timer); _recvReject = null; resolve(chunks); };
    _recvReject  = (err)    => { clearTimeout(timer); _recvResolve = null; _recvReject = null; _recvChunks = []; reject(err); };
  });
}

function reassemble(chunks) {
  const sorted = [...chunks].sort((a, b) => a[0] - b[0]);
  const parts = sorted.map(c => c.slice(2));
  const total = parts.reduce((s, p) => s + p.length, 0);
  const buf = new Uint8Array(total);
  let off = 0;
  for (const p of parts) { buf.set(p, off); off += p.length; }
  return buf;
}

function parseFrame(chunks) {
  const data = reassemble(chunks);
  // Minimum valid frame: 8 header + 0 payload + 2 CRC = 10 bytes
  if (data.length < 10) throw new Error("Frame too short");
  if (data[0] !== 0x55 || data[1] !== 0xAA) throw new Error("Bad magic");
  // Validate CRC-16/MODBUS: last 2 bytes (LE) must match CRC of all preceding bytes
  const receivedCrc = data[data.length - 2] | (data[data.length - 1] << 8);
  const computedCrc = crc16Modbus(data.slice(0, -2));
  if (receivedCrc !== computedCrc) throw new Error("Bad CRC");
  const cmd = data[3];
  const payloadLen = (data[6] << 8) | data[7];
  const payload = data.slice(8, 8 + payloadLen);
  return { cmd, payload };
}

// ── Token ─────────────────────────────────────────────────────────────────────
function randomToken() {
  const arr = new Uint8Array(16);
  crypto.getRandomValues(arr);
  return Array.from(arr).map(b => b.toString(16).padStart(2, "0")).join("");
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setPairStatus(cls, html) {
  const el = document.getElementById("pair-status");
  if (!el) return;
  el.className = "status-box " + cls;
  el.innerHTML = html;
}

function makeSpinnerHtml(msg) {
  return "<span style=\"display:flex;align-items:center;gap:8px\">" +
         "<span class=\"spinner\" aria-hidden=\"true\"></span>" + esc(msg) + "</span>";
}

function showSpinner(msg) {
  setPairStatus("status-info", makeSpinnerHtml(msg));
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Return the byte length of a string in UTF-8 encoding.
// JavaScript .length counts UTF-16 code units; WiFi SSID/password limits are in bytes.
function countUtf8Bytes(str) {
  // TextEncoder is available in all modern browsers and Node.js
  if (typeof TextEncoder !== "undefined") {
    return new TextEncoder().encode(str).length;
  }
  // Fallback for environments without TextEncoder (e.g. old test runners)
  return new Blob([str]).size;
}

// ── SSE ───────────────────────────────────────────────────────────────────────
function listenForActivation(token) {
  if (_currentEventSource) {
    _currentEventSource.close();
    _currentEventSource = null;
  }
  // Cancel any previous SSE timer before starting a new one — prevents
  // a stale timer from firing and corrupting state on rapid re-pairing.
  if (_activeSseTimer !== null) { clearTimeout(_activeSseTimer); _activeSseTimer = null; }
  const es = new EventSource(EVENTS_URL);
  _currentEventSource = es;
  const sseTimer = setTimeout(() => {
    es.close();
    _activeSseTimer = null;
    // Show timeout error so the user isn't left staring at a forever-spinner
    setPairStatus("status-warn", "\u23F1 " + esc(t("wifi_ap_timeout")));
    const pairBtn = document.getElementById("btn-pair");
    if (pairBtn) pairBtn.disabled = false;
    // Re-enable Back button so the user can return to credentials and adjust settings
    const backBtnSse = document.getElementById("btn-back-ble");
    if (backBtnSse) backBtnSse.disabled = false;
    dbg("BLE pairing: activation timeout after " + (SSE_TIMEOUT_MS / 1000) + "s");
  }, SSE_TIMEOUT_MS);
  _activeSseTimer = sseTimer;
  es.addEventListener("activated", (e) => {
    try {
      const d = JSON.parse(e.data);
      // Accept if token matches, OR if the event has no token (null/undefined = backward
      // compat with old server builds that omit the field). Reject events with a
      // different non-null token — they belong to a concurrent pairing session.
      if ((d.token == null || d.token === token) && d.gw_id && d.local_key) {
        clearTimeout(sseTimer); _activeSseTimer = null;
        es.close();
        setPairStatus("status-success", esc(t("success_activated")));
        // dbg() uses textContent → auto-escapes; do not call esc() here (would double-escape)
        dbg("Device activated: " + d.gw_id + " \u2713");
        // PLAT-811: Persist the SSID used for successful activation (with 90-day TTL)
        if (_ssid) { saveLastSsid(_ssid); }
        showDone(d.gw_id, d.local_key, d.ip_address || "");
        // Guard: btn-pair may be absent in test environments (onerror handler also guards)
        const pairBtnActivated = document.getElementById("btn-pair");
        if (pairBtnActivated) pairBtnActivated.disabled = false;
        const backBtnActivated = document.getElementById("btn-back-ble");
        if (backBtnActivated) backBtnActivated.disabled = false;
      } else if (d.gw_id === undefined || d.local_key === undefined) {
        dbg("SSE activated: missing gw_id or local_key");
      }
    } catch (err) {
      dbg("SSE parse error: " + err.message);
      clearTimeout(sseTimer); _activeSseTimer = null;
      es.close();
      setPairStatus("status-error", "\u274C " + esc(t("err_sse_lost")));
      const pairBtnErr = document.getElementById("btn-pair");
      if (pairBtnErr) pairBtnErr.disabled = false;
      const backBtnErr = document.getElementById("btn-back-ble");
      if (backBtnErr) backBtnErr.disabled = false;
    }
  });
  es.onerror = () => {
    clearTimeout(sseTimer); _activeSseTimer = null;
    es.close();
    // Re-enable pairing button and back button so user can retry or adjust credentials
    setPairStatus("status-error", "\u274C " + esc(t("err_sse_lost")));
    const pairBtn = document.getElementById("btn-pair");
    if (pairBtn) pairBtn.disabled = false;
    const backBtnOerr = document.getElementById("btn-back-ble");
    if (backBtnOerr) backBtnOerr.disabled = false;
  };
  return es;
}

// ── WiFi AP inline status helper ──────────────────────────────────────────────
function setWifiApStatus(cls, html) {
  const el = document.getElementById("wifi-ap-status");
  if (!el) return;
  el.className = "status-box " + cls;
  el.innerHTML = html;
  // Errors require immediate screen-reader announcement; switch to assertive live region.
  if (cls === "status-error") {
    el.setAttribute("role", "alert");
    el.setAttribute("aria-live", "assertive");
  } else {
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
  }
}

function showWifiApSpinner(msg) {
  setWifiApStatus("status-info", makeSpinnerHtml(msg));
}

// ── WiFi AP pairing flow ───────────────────────────────────────────────────────
async function pairViaWifiAp() {
  const btn = document.getElementById("btn-next");
  btn.disabled = true;
  const cancelBtn = document.getElementById("btn-cancel-wifi-ap");
  const backBtn   = document.getElementById("btn-back");
  if (cancelBtn) { cancelBtn.classList.remove("hidden"); cancelBtn.disabled = false; }
  if (backBtn) backBtn.classList.add("hidden");

  showWifiApSpinner(t("wifi_ap_connecting"));
  dbg("WiFi AP pair: POST /wifi-ap-pair for " + _selectedApSsid);

  // Attach SSE listener BEFORE the POST to avoid race where device activates
  // before the EventSource is established.
  const es = new EventSource(EVENTS_URL);
  if (_currentEventSource && _currentEventSource !== es) _currentEventSource.close();
  _currentEventSource = es;
  let token = null;
  let wifiApTimer = null;
  let _wifiApDone = false;  // dedup guard — first event wins

  const wifiApCleanup = (enableBtn) => {
    if (_wifiApDone) return;  // already handled
    _wifiApDone = true;
    es.close();
    clearTimeout(wifiApTimer);
    // Clear module-level tracking so navigation functions don't double-clear
    _activeSseTimer = null;
    if (cancelBtn) cancelBtn.classList.add("hidden");
    if (backBtn) backBtn.classList.remove("hidden");
    if (enableBtn) btn.disabled = false;
  };

  es.addEventListener("activated", (e) => {
    try {
      const d = JSON.parse(e.data);
      // d.token == null: backward compat — old server omitted token field; accept.
      // d.token === token: normal match.
      // Reject events with a non-null, non-matching token even if our token is
      // not yet known (POST still in flight) — avoids accepting a concurrent
      // user's activation during the brief pre-token window.
      if ((d.token == null || d.token === token) && d.gw_id && d.local_key) {
        wifiApCleanup(true);
        if (_ssid) saveLastSsid(_ssid);  // save only on confirmed activation
        setWifiApStatus("status-success", esc(t("success_activated")));
        // dbg() uses textContent → auto-escapes; do not call esc() here (would double-escape)
        dbg("Device activated: " + d.gw_id + " \u2713");
        showDone(d.gw_id, d.local_key, d.ip_address || "");
      } else if (d.gw_id === undefined || d.local_key === undefined) {
        dbg("SSE activated: missing gw_id or local_key in payload");
      }
    } catch (err) {
      dbg("SSE parse error (activated): " + err.message);
      wifiApCleanup(true);
      setWifiApStatus("status-error", "\u274C " + esc(t("wifi_ap_error")));
    }
  });
  es.addEventListener("wifi_ap_error", (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.token == null || d.token === token) {
        wifiApCleanup(true);
        setWifiApStatus("status-error", "\u274C " + esc(t("wifi_ap_error")));
        dbg("WiFi AP error: " + (d.error || "unknown"));
      }
    } catch (err) {
      dbg("SSE parse error (wifi_ap_error): " + err.message);
      wifiApCleanup(true);
      setWifiApStatus("status-error", "\u274C " + esc(t("wifi_ap_error")));
    }
  });
  es.onerror = () => {
    if (!_wifiApDone) {
      wifiApCleanup(true);
      setWifiApStatus("status-error", "\u274C " + esc(t("wifi_ap_error")));
      dbg("WiFi AP: SSE connection lost");
    }
  };

  try {
    // Use AbortController so a stalled network doesn't leave the button disabled forever
    const fetchAbort = new AbortController();
    const fetchTimeout = setTimeout(() => fetchAbort.abort(), 15000);
    let r;
    try {
      r = await fetch(_PROVISION_BASE + "/wifi-ap-pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ap_ssid: _selectedApSsid,
          home_ssid: _ssid,
          home_password: _pwd,
        }),
        signal: fetchAbort.signal,
      });
    } finally {
      clearTimeout(fetchTimeout);
    }
    if (!r.ok) {
      // Log the raw HTTP status for diagnostics; show a localized message to the user.
      dbg("wifi-ap-pair: server returned HTTP " + r.status);
      const msg = r.status === 409
        ? (t("wifi_ap_in_progress") || "Pairing already in progress — wait and retry.")
        : r.status === 503
          ? (t("wifi_ap_nmcli_missing") || "WiFi control unavailable — nmcli is not installed on the HA host.")
          : (t("wifi_ap_error") || "WiFi AP pairing failed.");
      wifiApCleanup(true);
      throw new Error(msg);
    }
    const data = await r.json();
    token = data.token;

    // After SSE_TIMEOUT_MS with no activation, show timeout error and re-enable button.
    // Guard: don't start if onerror already ran before POST completed.
    if (!_wifiApDone) {
      wifiApTimer = setTimeout(() => {
        wifiApCleanup(true);
        setWifiApStatus("status-error", "\u274C " + esc(t("wifi_ap_timeout") || t("wifi_ap_error")));
        dbg("WiFi AP pair: activation timeout after " + (SSE_TIMEOUT_MS / 1000) + "s");
      }, SSE_TIMEOUT_MS);
      // Track in module-level _activeSseTimer so navigation functions (goToDevices,
      // goToCredentials, beforeunload) can cancel it even though it's in a closure.
      _activeSseTimer = wifiApTimer;
    }

    showWifiApSpinner(t("wifi_ap_waiting"));
    dbg("WiFi AP pair: waiting for activation SSE\u2026");
  } catch (err) {
    // AbortError means the 15-second fetch watchdog fired — show a friendly message
    const msg = err.name === "AbortError"
      ? (t("wifi_ap_timeout") || "Pairing request timed out — please retry.")
      : err.message;
    wifiApCleanup(false);
    setWifiApStatus("status-error", "\u274C " + esc(msg));
    dbg("WiFi AP pair error: " + err.message);
    btn.disabled = false;
  }
}

// ── Main pairing flow ─────────────────────────────────────────────────────────
async function startPairing() {
  if (_pairMethod === PAIR_METHOD.WIFI_AP) {
    await pairViaWifiAp();
    return;
  }

  const btn = document.getElementById("btn-pair");
  btn.disabled = true;
  const backBtn = document.getElementById("btn-back-ble");
  if (backBtn) backBtn.disabled = true;

  // Reset BLE receive buffers — defensive guard against stale state from a prior attempt
  _recvChunks = [];
  _recvResolve = null;
  _recvReject  = null;

  const token = randomToken();
  const activator = ACTIVATOR_URL;
  const es = listenForActivation(token);

  let server;
  let device = null;
  // Hoist cleanup references so catch block can access them even if the error
  // occurs after the listeners were added (const would be block-scoped to try).
  let _cleanupNotify = null;
  let _onDisconnected = null;

  try {
    dbg(t("spin_scanning"));
    showSpinner(t("spin_scanning"));
    device = await navigator.bluetooth.requestDevice({
      filters: [{ services: [BLE_SERVICE] }],
      optionalServices: [BLE_SERVICE],
    });

    const connMsg = t("spin_connecting", { name: device.name || device.id });
    dbg(connMsg);
    showSpinner(connMsg);
    server = await device.gatt.connect();
    const service    = await server.getPrimaryService(BLE_SERVICE);
    const writeChar  = await service.getCharacteristic(WRITE_CHAR);
    const notifyChar = await service.getCharacteristic(NOTIFY_CHAR);

    await notifyChar.startNotifications();
    notifyChar.addEventListener("characteristicvaluechanged", onNotify);
    _cleanupNotify = () => notifyChar.removeEventListener("characteristicvaluechanged", onNotify);

    // If the device disconnects mid-pairing (e.g. factory reset or link loss), fail
    // the current waitForResponse immediately rather than waiting for the 10-15s timeout.
    // Store handler so it can be removed and not accumulate across retries.
    _onDisconnected = () => {
      if (_recvReject) {
        const reject = _recvReject;
        _recvReject = null;
        reject(new Error("BLE device disconnected"));
      }
    };
    device.addEventListener("gattserverdisconnected", _onDisconnected);

    dbg(t("spin_handshake"));
    showSpinner(t("spin_handshake"));
    const controllerNonce = new Uint8Array(16);
    crypto.getRandomValues(controllerNonce);

    const hsFrame = encodeFrame(CMD_HANDSHAKE, 0, controllerNonce);
    for (const chunk of chunkFrame(hsFrame)) {
      await writeChar.writeValueWithoutResponse(chunk);
    }

    const hsChunks = await waitForResponse(10000);
    const hsResp = parseFrame(hsChunks);
    if (hsResp.payload.length < 16) throw new Error("Bad handshake nonce");

    dbg(t("spin_sending"));
    showSpinner(t("spin_sending"));
    const wifiPayload = JSON.stringify({
      s: _ssid, p: _pwd, t: token, r: "az", activator: activator,
    });
    const wifiBytes = new TextEncoder().encode(wifiPayload);
    const wifiFrame = encodeFrame(CMD_WIFI_CONFIG, 1, wifiBytes);
    for (const chunk of chunkFrame(wifiFrame)) {
      await writeChar.writeValueWithoutResponse(chunk);
    }

    const ackChunks = await waitForResponse(15000);
    const ack = parseFrame(ackChunks);
    if (ack.cmd === CMD_PAIR_FAIL) throw new Error(t("err_pair_fail"));

    dbg(t("spin_waiting"));
    showSpinner(t("spin_waiting"));

    if (typeof _cleanupNotify === "function") { _cleanupNotify(); _cleanupNotify = null; }
    if (device && _onDisconnected) { device.removeEventListener("gattserverdisconnected", _onDisconnected); _onDisconnected = null; }
    if (server && server.connected) {
      try { await server.disconnect(); } catch (_) {}
    }

  } catch (err) {
    if (typeof _cleanupNotify === "function") { _cleanupNotify(); _cleanupNotify = null; }
    if (device && _onDisconnected) { device.removeEventListener("gattserverdisconnected", _onDisconnected); _onDisconnected = null; }
    // Clear BLE receive buffers so stale chunks don't contaminate the next pairing attempt.
    // (waitForResponse's reject handler clears them in timeout/disconnect paths, but
    // exceptions thrown directly from the try block may skip that handler.)
    _recvChunks = []; _recvResolve = null; _recvReject = null;
    // Disconnect GATT server if it was opened — Bluetooth is an exclusive resource
    // and leaving the connection open blocks other apps and the next pairing attempt.
    if (server && server.connected) {
      try { await server.disconnect(); } catch (_) {}
    }
    es.close();
    // Cancel the SSE timeout so it doesn't overwrite the error message after 60 s
    if (_activeSseTimer !== null) { clearTimeout(_activeSseTimer); _activeSseTimer = null; }
    if (err.name === "NotFoundError" || err.name === "AbortError") {
      setPairStatus("status-warn", esc(t("warn_scan_cancelled")));
      dbg("BLE scan cancelled");
    } else if (err.name === "NotSupportedError") {
      setPairStatus("status-error", "\u274C " + esc(t("warn_ble_disabled")));
      dbg("BLE not supported (Bluetooth off?): " + err.message);
    } else {
      setPairStatus("status-error", "\u274C " + esc(err.message));
      dbg("BLE error: " + err.message);
    }
    btn.disabled = false;
    if (backBtn) backBtn.disabled = false;
  }
}

// ── Copy share URL ────────────────────────────────────────────────────────────
function copyShareUrl() {
  const input = document.getElementById("share-url");
  const btn = document.getElementById("btn-copy");
  const val = input ? input.value : "";
  let copyPromise;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    copyPromise = navigator.clipboard.writeText(val);
  } else {
    // Legacy fallback: execCommand("copy") requires the field to be selected first
    if (input) { input.select(); }
    copyPromise = Promise.resolve(document.execCommand("copy"));
  }
  copyPromise.then(() => {
    btn.textContent = t("qr_copy_done");
    setTimeout(() => { btn.textContent = t("qr_copy_btn"); }, 2000);
  }).catch((err) => {
    dbg("Copy to clipboard failed: " + err.message);
    // Show ✗ briefly so the user knows the copy did not succeed, then restore button label.
    btn.textContent = "\u2717";
    setTimeout(() => { btn.textContent = t("qr_copy_btn"); }, 2000);
  });
}

// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  dbg("Initializing\u2026");

  // Bind all event handlers via addEventListener (CSP: no inline handlers allowed)
  document.getElementById("lang-select").addEventListener("change", function() {
    changeLang(this.value);
  });
  document.getElementById("btn-wifi-scan").addEventListener("click", scanWifi);
  // btn-next is type="submit" inside wifi-form, so clicking it fires the form submit event.
  // We only bind the submit listener — NOT a separate click listener — to prevent
  // goToStep2() from being called twice (click fires first, then submit event fires).
  // In the WiFi AP path a double call would make two concurrent POST /wifi-ap-pair requests.
  document.getElementById("wifi-form").addEventListener("submit", (e) => {
    e.preventDefault();
    goToStep2();
  });
  document.getElementById("btn-back").addEventListener("click", goToDevices);
  document.getElementById("btn-back-ble").addEventListener("click", goToCredentials);
  document.getElementById("btn-cancel-wifi-ap").addEventListener("click", () => {
    // Close SSE + restore UI — user can retry or go back
    if (_currentEventSource) { _currentEventSource.close(); _currentEventSource = null; }
    // Cancel the WiFi AP timeout timer (tracked via _activeSseTimer) so it doesn't
    // overwrite the "cancelled" message with a stale "timeout" error 120 s later.
    if (_activeSseTimer !== null) { clearTimeout(_activeSseTimer); _activeSseTimer = null; }
    const cancelBtnEl = document.getElementById("btn-cancel-wifi-ap");
    const backBtnEl   = document.getElementById("btn-back");
    const pairBtnEl   = document.getElementById("btn-next");
    if (cancelBtnEl) cancelBtnEl.classList.add("hidden");
    if (backBtnEl)   backBtnEl.classList.remove("hidden");
    if (pairBtnEl)   pairBtnEl.disabled = false;
    setWifiApStatus("status-warn", "\u26A0 " + esc(t("warn_scan_cancelled")));
    dbg("WiFi AP pairing cancelled by user");
  });
  document.getElementById("btn-pair").addEventListener("click", startPairing);
  document.getElementById("btn-copy").addEventListener("click", copyShareUrl);
  document.getElementById("btn-ble-scan").addEventListener("click", selectDeviceBle);
  document.getElementById("btn-pair-another").addEventListener("click", goToDevices);
  document.getElementById("btn-refresh-scan").addEventListener("click", autoDetectDevices);

  // Close any open SSE connection when the user navigates away or closes the tab.
  // Without this, the server-side SSE queue persists until the HTTP connection drops
  // (which may take tens of seconds on some proxies), exhausting the queue limit.
  window.addEventListener("beforeunload", () => {
    if (_currentEventSource) {
      _currentEventSource.close();
      _currentEventSource = null;
    }
    if (_activeSseTimer !== null) {
      clearTimeout(_activeSseTimer);
      _activeSseTimer = null;
    }
  });

  // Escape key: cancel WiFi AP pairing when cancel button is visible
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const cancelBtn = document.getElementById("btn-cancel-wifi-ap");
    if (cancelBtn && !cancelBtn.classList.contains("hidden") && !cancelBtn.disabled) {
      cancelBtn.click();
    }
  });

  // Password show/hide toggle
  document.getElementById("btn-pwd-toggle").addEventListener("click", function() {
    const pwd = document.getElementById("password");
    const showing = pwd.type === "text";
    pwd.type = showing ? "password" : "text";
    this.textContent = showing ? "\uD83D\uDC41" : "\uD83D\uDE48";  // 👁 / 🙈
    this.setAttribute("aria-label", showing ? t("show_password") || "Show password" : t("hide_password") || "Hide password");
    this.setAttribute("aria-pressed", String(!showing));
  });

  // QR image fallback: hide img and show unavail text on error
  const qrImg = document.getElementById("qr-img");
  if (qrImg) {
    qrImg.addEventListener("error", function() {
      this.style.display = "none";
      const unavail = document.getElementById("qr-unavail");
      if (unavail) unavail.style.display = "block";
    });
  }

  // iOS autocomplete fix: fields start readonly, become writable on focus
  ["ssid", "password"].forEach(id => {
    const el = document.getElementById(id);
    el.setAttribute("readonly", "");
    el.addEventListener("focus", () => el.removeAttribute("readonly"));
  });

  // 1. Detect and load language
  const lang = detectLang();
  document.getElementById("lang-select").value = lang;
  document.documentElement.lang = lang;
  await loadLang(lang);
  applyStrings();
  updateStepCounter(1);

  // 2. PLAT-810: Browser compatibility check — must run after strings are loaded
  // so the error panel shows the correct i18n text.
  // Show a hard error if the browser has no Web Bluetooth AND is not a
  // supported browser (Chrome, Edge, or Safari). This catches Firefox and
  // other browsers that will never support Web Bluetooth.
  if (!hasWebBluetooth() && !isSupportedBrowser()) {
    dbg("ERROR: Browser does not support Web Bluetooth and is not a supported browser.");
    document.getElementById("err-browser-unsupported").classList.remove("hidden");
    document.getElementById("panel-devices").classList.add("hidden");
    // No point loading server config or continuing init for an unsupported browser
    return;
  }

  // 3. Load server config (activator URL + default SSID)
  await loadServerConfig();

  // 3b. Auto-detect Tuya devices in AP mode (fast, no rescan, no user gesture needed)
  document.getElementById("panel-devices").classList.remove("hidden");
  autoDetectDevices();

  // 4. Secure context check — show warning in BLE panel but keep WiFi form working
  if (!window.isSecureContext) {
    dbg("WARNING: Not a secure context (HTTP). BLE requires HTTPS.");
    document.getElementById("warn-https").classList.remove("hidden");
    document.getElementById("btn-pair").disabled = true;
  }

  // 5. Web Bluetooth availability check
  if (!navigator.bluetooth) {
    dbg("WARNING: Web Bluetooth not available in this browser.");
    document.getElementById("btn-pair").disabled = true;

    if (isIOS()) {
      // iOS — Web Bluetooth not available in any iOS browser (Apple restriction).
      // Show a tailored message instead of a useless QR code.
      dbg("iOS detected — Web Bluetooth not supported on this platform.");
      document.getElementById("warn-browser").classList.remove("hidden");
      document.getElementById("err-browser-body").textContent =
        t("err_ios_no_webbluetooth") ||
        "Web Bluetooth is not available on iOS. Please open this page on a computer or Android device using Chrome or Edge.";
    } else if (isAndroid()) {
      // Android with a non-Chrome browser — offer a direct link to open in Chrome.
      dbg("Android detected — showing Chrome deep link.");
      document.getElementById("warn-browser").classList.remove("hidden");
      const intentUrl = chromeIntentUrl();
      if (intentUrl) {
        const btn = document.createElement("a");
        btn.href = intentUrl;
        btn.className = "btn btn-primary btn-open-chrome";
        btn.id = "btn-open-chrome";
        btn.innerHTML =
          "<svg width='16' height='16' viewBox='0 0 24 24' fill='none' aria-hidden='true'>" +
          "<circle cx='12' cy='12' r='10' stroke='currentColor' stroke-width='2'/>" +
          "<circle cx='12' cy='12' r='4' fill='currentColor'/>" +
          "</svg>" +
          esc(t("open_in_chrome") || "Open in Chrome");
        document.getElementById("warn-browser").appendChild(btn);
        // Fallback note shown after a short delay if Chrome did not open.
        btn.addEventListener("click", () => {
          setTimeout(() => {
            const note = document.getElementById("chrome-not-installed");
            if (note) note.classList.remove("hidden");
          }, 2500);
        });
        const note = document.createElement("p");
        note.id = "chrome-not-installed";
        note.className = "step-hint hidden";
        note.style.marginTop = "6px";
        note.textContent =
          t("chrome_not_installed") ||
          "Chrome does not appear to be installed. Install Chrome from the Play Store and try again.";
        document.getElementById("warn-browser").appendChild(note);
      }
    } else {
      // Desktop or unknown — show generic message + QR/share section.
      document.getElementById("warn-browser").classList.remove("hidden");
      if (window.isSecureContext) {
        document.getElementById("qr-section").classList.remove("hidden");
        document.getElementById("share-url").value = window.location.href;
      }
    }
  }
})();

// Exported for unit testing only — not used in the browser.
if (typeof module !== "undefined") {
  module.exports = {
    PAIR_METHOD, SSE_TIMEOUT_MS,
    isSupportedBrowser, hasWebBluetooth,
    isIOS, isAndroid, chromeIntentUrl,
    saveLastSsid, loadLastSsid, SSID_TTL_MS, SSID_STORAGE_KEY,
    autoDetectDevices, showDeviceCard, selectDeviceWifiAp, selectDeviceBle,
    goToDevices, goToCredentials, goToStep2, showDone,
    countUtf8Bytes, reassemble, onNotify, waitForResponse, parseFrame, crc16Modbus,
    _safePath, _safeUrl, _t: t,
    _getRecvReject: () => _recvReject,
    _getActiveSseTimer: () => _activeSseTimer,
    _listenForActivation: listenForActivation,
    _updateStepCounter: updateStepCounter,
  };
}
