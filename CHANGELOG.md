# Changelog

All notable changes to Tuya Cloudless are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.5.3] — 2026-03-18

### Fixed
- `PairingRedirectView` now checks `X-Forwarded-Proto` header before redirecting.
  When HA runs behind an HTTPS reverse proxy (nginx, Traefik, Caddy, etc.) the
  browser request arrives at HA with `X-Forwarded-Proto: https`.  The redirect
  view now sends the browser to the HA-hosted HTTPS pairing UI
  (`/api/tuya_cloudless/pairing/`) instead of the HTTP port-8099 server, keeping
  `isSecureContext = true` so Web Bluetooth works — even without `external_url`
  configured in HA.

### Testing
- 4 new unit tests for `PairingRedirectView` (HTTPS redirect, HTTP fallback,
  empty host, default port).

## [0.5.2] — 2026-03-18

### Fixed
- HTTPS detection now tries both internal and external HA URLs (covers
  Let's Encrypt, reverse proxy with external_url set, and Nabu Casa).
- When no HTTPS URL is found, the flow no longer aborts — a new
  `ble_fallback` step is shown explaining that HTTPS is required for BLE
  and offering **Search** or **Manual** setup as alternatives.
- `https_required` abort text updated: "Tuya Cloudless requires HTTPS for
  BLE pairing" with clearer guidance.

### Testing
- 3 new Playwright tests for the `warn-https` panel (isSecureContext false/true).
- `TestBleFallbackStep` — 4 new unit tests for the ble_fallback step logic.
- `test_proceeds_when_external_url_is_https` — covers Nabu Casa / Let's Encrypt.

## [0.5.1] — 2026-03-18

### Fixed
- Pairing UI now served over HA's HTTPS server so Web Bluetooth works when HA
  runs with HTTPS. Previously the browser was redirected to `http://ha:8099/`
  (HTTP), causing `isSecureContext = false` and disabling the BLE button even
  though HA itself was HTTPS-configured.
- `app.js` base paths configurable via injected `window._TUYA_*` globals;
  direct port-8099 access continues to work unchanged.
- Improved `error_https_required_body` i18n text in all 21 languages to
  explain that HA must be configured with HTTPS.

## [0.5.0] — 2026-03-18

### Security
- Rate limiting on pairing activation endpoint (max 10 requests/60 s per IP)
- Bounded SSE queues (`maxsize=32`) and connection cap (max 10 simultaneous SSE clients)
- Strict CORS on result/SSE endpoints (wildcard `Access-Control-Allow-Origin` removed)
- `local_key` ASCII + printable character validation in all config flow steps
- IP address format validated with `ipaddress.ip_address()` in all config flow steps
- Documented CBC zero-IV limitation for protocol v3.1–3.3 in `SECURITY.md`

### Robustness
- Guard against duplicate `_connection_loop` tasks on coordinator restart
- `SESSION_KEY_NEG_TIMEOUT` extracted as a named constant (5.0 s)
- `send_raw_dps` service validated with a voluptuous schema
- Binary sensor now prefers `dp_value.id` over `dp_power.id` as primary DP
- `asyncio.create_task` used instead of `ensure_future` (named task for debugging)

### Quality
- `quality_scale`: `bronze` → `silver`
- Minimum Home Assistant version: `2024.4.0` → `2025.1.0`
- All 19 translation files complete (131/131 keys each)
- `DeviceInfo.serial_number` set to `gw_id`
- `ConfigFlow.MINOR_VERSION = 1` added for config entry versioning

### Home Assistant integration
- Diagnostics: DPS values sanitised before exposure (bytes and long strings redacted)
- Repair flow: connectivity repair now redirects to reconfigure flow on confirm
- Update entity: `device_class = None`, `translation_key = "protocol_version"`

### Testing
- **100% test coverage** — 2621 statements, 0 missed across all modules
- 1557 unit tests (up from 1493)
- Playwright E2E suite: 22 tests covering the full BLE pairing UI flow

### Developer experience
- `scripts/run-checks-python.sh` — Python-only check script (skips UI tests)
- `RUN_UI_TESTS=0` environment variable support in `run-checks.sh`

## [0.2.4] — 2026-03-15

### Fixed
- Config flow failed to load ("Invalid handler specified") for users installing from
  the git repository directly (e.g. via HACS custom repo without a release zip, or
  manual copy). `lib/tuya_cloudless` is now committed inside `custom_components/`
  so all installation methods work without bundling.
- `manifest.json` had incorrect GitHub URLs (`kvista-se` → `11z4t`).

## [0.2.3] — 2026-03-15

### Fixed
- Device profiles not loading on HACS installations: `PROFILES_DIR` now resolves
  to `custom_components/tuya_cloudless/profiles/` (bundled) rather than the repo
  root `profiles/` (which does not exist in installed environments). Release zip
  now bundles all 18 YAML profiles inside the integration directory.

## [0.2.2] — 2026-03-15

### Fixed
- Config flow failed to load ("Invalid handler specified") because `lib/tuya_cloudless`
  was missing from HACS installations. Release zip now bundles the library at
  `tuya_cloudless/lib/tuya_cloudless/` where `__init__.py` already looks for it.

## [0.2.1] — 2026-03-15

### Fixed
- HACS v2 compatibility: release workflow now builds and uploads `tuya_cloudless.zip`
  as a release asset so HACS can download the integration correctly

## [0.2.0] — 2026-03-15

### Added
- **number** platform — numeric value entities (brightness, volume, etc.)
- **select** platform — enum selector entities (modes, scenes, etc.)
- **climate** platform — thermostat / AC / heater with full HVAC mode support (heat, cool, auto, fan_only, dry)
- **fan** platform — speed, preset modes, oscillation, direction
- 14 new device profiles: Electric Heater, Smart Thermostat, Smart Dimmer, Ceiling Fan,
  Water Leak Sensor, PIR Motion Sensor, Door/Window Sensor, Smoke Detector, CO₂ Sensor,
  Siren/Alarm, AC Cool/Heat, Floor Heating, Ventilation, Heat Pump
- `dp_mode`, `dp_temp_set`, `dp_temp_current`, `dp_oscillate`, `dp_direction`, `dp_options`,
  `target_min`, `target_max`, `step` fields added to EntitySpec
- Clickable pairing tool link (`http://<ha-host>:8099`) in all config flow dialogs
  that require entering a local key
- `extra_state_attributes` on climate and fan entities exposing raw DP values for diagnostics
- Per-HA `asyncio.Lock` for profile loading (eliminates race condition under concurrent setup)
- `TestCoordinatorInit`, `TestClimateInit`, `TestFanInit` constructor test classes

### Fixed
- CMD constant conflict between CONTROL (0x07) and STATUS (0x07) in protocol.py
- Binary sensor availability no longer shadows DP state (was always unavailable)
- Auth failure now triggers HA re-auth repair flow instead of silently failing
- Fan percentage correctly mapped using `min_raw`/`max_raw` from DPSpec
- Cover STOP command properly supported
- GCM tag split fixed: `encrypt()` returns `ct+tag`, not `ct` alone
- `async_unload_entry` removes inserted lib path from `sys.path` when last entry unloads
- Climate `hvac_mode` fallback changed from HEAT to AUTO (more neutral when mode DP is missing)
- `entity_specs` stored as frozen tuple in RuntimeData
- `_PROFILES_LOADED` moved from global to per-hass `hass.data[DOMAIN]`
- `device_name` defaults to profile name when not set in config entry

### Changed
- Scale convention unified across all platforms: `display = raw * scale`

## [0.1.0] — 2026-03-01

### Added
- Repo scaffolding, CI/CD pipeline (ruff, mypy --strict, bandit, detect-secrets, pytest)
- Custom component skeleton (config flow, coordinator, entity base, strings EN+SV)
- Tuya LAN protocol parser v3.1–3.5 (frame encode/decode, DPS handling)
- Crypto module — AES-ECB (v3.1–3.3) + AES-GCM (v3.4–3.5) + X25519 ECDH session key
- UDP discovery listener (ports 6666/6667, async, automatic device detection)
- **switch** platform
- **light** platform (on/off, brightness, color temperature)
- **sensor** platform (numeric DP values with scale, unit, device_class)
- **binary_sensor** platform
- **cover** platform (open, close, set position, stop)
- 4 built-in device profiles: Generic Switch, Generic Light, Smart Plug, Roller Blind
- Options flow (heartbeat interval, command timeout, reconnect delay)
- Re-authentication flow (triggered automatically on auth failure)
- Reconfigure flow (update IP/key without removing the device)
- Diagnostics (redacts local key)
- EN + SV translations
