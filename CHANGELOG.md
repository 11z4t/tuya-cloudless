# Changelog

All notable changes to Tuya Cloudless are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
