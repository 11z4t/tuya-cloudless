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

// ── Debug log ─────────────────────────────────────────────────────────────────
function dbg(msg) {
  const ts = new Date().toLocaleTimeString();
  const el = document.getElementById("debug-log");
  if (el) {
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
    const r = await fetch("/static/i18n/" + lang + ".json");
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
    // Provide built-in fallbacks for critical keys so UI never shows raw key names
    const fallbacks = {
      btn_next: "Next \u2192",
      step_x_of_y: "Step {x} of {y}",
      wifi_scan_none: "No networks found",
      debug_title: "Debug log",
      help_text: "Need help?",
      help_link_label: "Open guide \u2192",
    };
    s = fallbacks[key] !== undefined ? fallbacks[key] : key;
  }
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.replaceAll("{" + k + "}", v);
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
  el("btn-pair-label").innerHTML   = t("btn_scan");
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
  loadLang(lang).then(applyStrings);
  document.documentElement.lang = lang;
}

function detectLang() {
  const stored = localStorage.getItem("tc-lang");
  if (stored && SUPPORTED_LANGS.includes(stored)) return stored;
  const nav = (navigator.language || "en").substring(0, 2).toLowerCase();
  return SUPPORTED_LANGS.includes(nav) ? nav : "en";
}

// ── Step counter ──────────────────────────────────────────────────────────────
let _currentStep = 1;
const _TOTAL_STEPS = 2;

function updateStepCounter(step) {
  _currentStep = step;
  const el = document.getElementById("step-counter");
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
  } catch (_) {}
}

function loadLastSsid() {
  try {
    const raw = localStorage.getItem(SSID_STORAGE_KEY);
    if (!raw) return null;
    const { ssid, saved_at } = JSON.parse(raw);
    if (Date.now() - saved_at > SSID_TTL_MS) {
      localStorage.removeItem(SSID_STORAGE_KEY);
      return null;
    }
    return ssid;
  } catch (_) { return null; }
}

// ── Server config ─────────────────────────────────────────────────────────────
let ACTIVATOR_URL = window.location.origin;
let EVENTS_URL    = ACTIVATOR_URL + "/api/provision/events";

const _urlParams = new URLSearchParams(window.location.search);
const _haFlowId  = _urlParams.get("flow_id") || null;

async function loadServerConfig() {
  try {
    const r = await fetch("/api/provision/config");
    if (r.ok) {
      const cfg = await r.json();
      if (cfg.activator_url) ACTIVATOR_URL = cfg.activator_url;
      if (cfg.events_url)    EVENTS_URL    = cfg.events_url;
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
    }
  } catch (_) { /* keep defaults */ }
}

// ── WiFi scan ─────────────────────────────────────────────────────────────────
async function scanWifi() {
  const btn = document.getElementById("btn-wifi-scan");
  btn.disabled = true;
  dbg("WiFi scan started\u2026");
  // Close dropdown if already open
  document.getElementById("wifi-dropdown").classList.add("hidden");
  try {
    const r = await fetch("/api/provision/wifi-scan");
    if (!r.ok) throw new Error("scan HTTP " + r.status);
    const data = await r.json();
    const ssids = data.ssids || [];
    dbg("WiFi scan: " + ssids.length + " networks found");
    showWifiDropdown(ssids);
  } catch (err) {
    dbg("WiFi scan error: " + err.message);
    showWifiDropdown([]);
  } finally {
    btn.disabled = false;
  }
}

function showWifiDropdown(ssids) {
  const dd = document.getElementById("wifi-dropdown");
  dd.innerHTML = "";
  if (ssids.length === 0) {
    const item = document.createElement("div");
    item.className = "wifi-option muted";
    item.textContent = t("wifi_scan_none");
    dd.appendChild(item);
  } else {
    for (const ssid of ssids) {
      const item = document.createElement("div");
      item.className = "wifi-option";
      item.textContent = ssid;
      item.addEventListener("click", () => selectWifi(ssid));
      dd.appendChild(item);
    }
  }
  dd.classList.remove("hidden");
}

function selectWifi(ssid) {
  document.getElementById("ssid").value = ssid;
  document.getElementById("wifi-dropdown").classList.add("hidden");
}

// Close dropdown when clicking outside
document.addEventListener("click", (e) => {
  const dd = document.getElementById("wifi-dropdown");
  const ssidInput = document.getElementById("ssid");
  const scanBtn = document.getElementById("btn-wifi-scan");
  if (!dd.contains(e.target) && e.target !== ssidInput && e.target !== scanBtn) {
    dd.classList.add("hidden");
  }
});

// ── Step navigation ───────────────────────────────────────────────────────────
let _ssid = "";
let _pwd  = "";

function goToStep2() {
  _ssid = document.getElementById("ssid").value.trim();
  const errEl = document.getElementById("s1-error");
  if (!_ssid) {
    errEl.className = "status-box status-warn";
    errEl.textContent = t("warn_no_ssid");
    return;
  }
  errEl.className = "status-box hidden";
  _pwd = document.getElementById("password").value;

  document.getElementById("panel-wifi").classList.add("hidden");
  document.getElementById("panel-ble").classList.remove("hidden");
  updateStepCounter(2);
  dbg("Step 2: BLE pairing");
}

function showDone(gw_id, local_key, ip_address) {
  document.getElementById("panel-ble").classList.add("hidden");
  document.getElementById("panel-done").classList.remove("hidden");
  updateStepCounter(3);  // hides counter

  const grid = document.getElementById("result-grid");
  grid.innerHTML =
    "<span class=\"result-label\">" + esc(t("label_device_id")) + "</span>" +
    "<span class=\"result-value\">" + esc(gw_id) + "</span>" +
    "<span class=\"result-label\">" + esc(t("label_ip")) + "</span>" +
    "<span class=\"result-value\">" + esc(ip_address) + "</span>" +
    "<span class=\"result-label\">" + esc(t("label_local_key")) + "</span>" +
    "<span class=\"result-value key-value\">" + esc(local_key) + "</span>";

  if (_haFlowId) {
    document.getElementById("ha-flow-msg").classList.remove("hidden");
  } else {
    const params = new URLSearchParams({ domain: "tuya_cloudless", gw_id, local_key, ip_address });
    document.getElementById("btn-add-ha").href = "/config/integrations/add?" + params;
    document.getElementById("btn-add-ha").classList.remove("hidden");
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

function onNotify(event) {
  const data = new Uint8Array(event.target.value.buffer);
  _recvChunks.push(data);
  const chunkNo = data[0], total = data[1];
  if (chunkNo + 1 === total && _recvResolve) {
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
      reject(new Error("BLE response timeout"));
    }, timeoutMs);
    _recvResolve = (chunks) => { clearTimeout(timer); resolve(chunks); };
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
  if (data.length < 10) throw new Error("Frame too short");
  if (data[0] !== 0x55 || data[1] !== 0xAA) throw new Error("Bad magic");
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
  el.className = "status-box " + cls;
  el.innerHTML = html;
}

function showSpinner(msg) {
  setPairStatus("status-info",
    "<span style=\"display:flex;align-items:center;gap:8px\"><span class=\"spinner\"></span>" + esc(msg) + "</span>");
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ── SSE ───────────────────────────────────────────────────────────────────────
function listenForActivation(token) {
  const es = new EventSource(EVENTS_URL);
  es.addEventListener("activated", (e) => {
    try {
      const d = JSON.parse(e.data);
      if (!token || d.token === token || !d.token) {
        es.close();
        setPairStatus("status-success", t("success_activated"));
        dbg("Device activated: " + d.gw_id + " \u2713");
        // PLAT-811: Persist the SSID used for successful activation (with 90-day TTL)
        if (_ssid) { saveLastSsid(_ssid); }
        showDone(d.gw_id, d.local_key, d.ip_address);
        document.getElementById("btn-pair").disabled = false;
      }
    } catch (_) {}
  });
  es.onerror = () => es.close();
  setTimeout(() => es.close(), 120000);
  return es;
}

// ── Main pairing flow ─────────────────────────────────────────────────────────
async function startPairing() {
  const btn = document.getElementById("btn-pair");
  btn.disabled = true;

  const token = randomToken();
  const activator = ACTIVATOR_URL;
  const es = listenForActivation(token);

  let server;

  try {
    dbg(t("spin_scanning"));
    showSpinner(t("spin_scanning"));
    const device = await navigator.bluetooth.requestDevice({
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
    if (ack.cmd === CMD_PAIR_FAIL) throw new Error("Device rejected WiFi config");

    dbg(t("spin_waiting"));
    showSpinner(t("spin_waiting"));

    if (server.connected) server.disconnect();

  } catch (err) {
    es.close();
    if (err.name === "NotFoundError" || err.name === "AbortError") {
      setPairStatus("status-warn", t("warn_scan_cancelled"));
      dbg("BLE scan cancelled");
    } else {
      setPairStatus("status-error", "\u274C " + esc(err.message));
      dbg("BLE error: " + err.message);
    }
    btn.disabled = false;
  }
}

// ── Copy share URL ────────────────────────────────────────────────────────────
function copyShareUrl() {
  const val = document.getElementById("share-url").value;
  const btn = document.getElementById("btn-copy");
  (navigator.clipboard
    ? navigator.clipboard.writeText(val)
    : Promise.resolve(document.execCommand("copy"))
  ).then(() => {
    btn.textContent = t("qr_copy_done");
    setTimeout(() => { btn.textContent = t("qr_copy_btn"); }, 2000);
  }).catch(() => {});
}

// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  dbg("Initializing\u2026");

  // Bind all event handlers via addEventListener (CSP: no inline handlers allowed)
  document.getElementById("lang-select").addEventListener("change", function() {
    changeLang(this.value);
  });
  document.getElementById("btn-wifi-scan").addEventListener("click", scanWifi);
  document.getElementById("btn-next").addEventListener("click", goToStep2);
  document.getElementById("btn-pair").addEventListener("click", startPairing);
  document.getElementById("btn-copy").addEventListener("click", copyShareUrl);

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
    document.getElementById("panel-wifi").classList.add("hidden");
    // No point loading server config or continuing init for an unsupported browser
    return;
  }

  // 3. Load server config (activator URL + default SSID)
  await loadServerConfig();

  // 4. Secure context check — show warning in BLE panel but keep WiFi form working
  if (!window.isSecureContext) {
    dbg("WARNING: Not a secure context (HTTP). BLE requires HTTPS.");
    document.getElementById("warn-https").classList.remove("hidden");
    document.getElementById("btn-pair").disabled = true;
  }

  // 5. Web Bluetooth availability check
  if (!navigator.bluetooth) {
    dbg("WARNING: Web Bluetooth not available in this browser.");
    document.getElementById("warn-browser").classList.remove("hidden");
    document.getElementById("btn-pair").disabled = true;
    // Show QR/share section so user can open on a supported device
    if (window.isSecureContext) {
      document.getElementById("qr-section").classList.remove("hidden");
      document.getElementById("share-url").value = window.location.href;
    }
  }
})();

// Exported for unit testing only — not used in the browser.
if (typeof module !== "undefined") {
  module.exports = { isSupportedBrowser, hasWebBluetooth, saveLastSsid, loadLastSsid, SSID_TTL_MS, SSID_STORAGE_KEY };
}
