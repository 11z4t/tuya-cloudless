# Deep Code Review — Tuya Cloudless

**Reviewer:** Claude Code (Sonnet 4.6)
**Date:** 2026-03-15
**Branch:** plat-903-implementation
**Commit:** 7933d48
**Scope:** Full codebase — lib/, custom_components/, profiles/, tests/

> **Benchmark:** Home Assistant Shelly integration = 8/10.
> **Goal:** Identify what it takes to reach 10/10.

---

## 1. Executive Verdict

**Current score: 7.5 / 10**

Tuya Cloudless is architecturally mature for a v0.1 custom integration. The layer separation (lib ↔ HA wrapper) is enforced by tooling. The protocol implementation covers v3.1–3.5 including AES-GCM + ECDH session key negotiation. The entity model is profile-driven (YAML), which is Tuya's killer feature versus every other open implementation. The quality toolchain (mypy --strict, bandit, detect-secrets, ruff) is genuinely production-grade.

**What holds it back from Shelly-level quality:**

1. The `sys.path` hack in `__init__.py` is a deployment landmine for HACS.
2. Device names default to the raw gateway ID hex string — awful UX.
3. Scale conventions are **inverted** between climate and number/sensor — a YAML authoring trap.
4. Command code constants in `const.py` and `message.py` conflict with each other.
5. Repair flows do nothing useful — they just confirm. No guided remediation.
6. The `devices()` async iterator in `DiscoveryListener` has a semantic bug (re-yields all known devices on each event).
7. `binary_sensor.py` uses `__import__("contextlib")` — anti-pattern that bypasses static analysis.
8. Entity `device_info.name` is `gw_id` instead of profile name, and `device_info.model` is hardcoded to `"Tuya Cloudless"`.
9. `wait_for_device()` uses deprecated `asyncio.get_event_loop()`.
10. No `STOP` support for cover entities despite most Tuya motors supporting it.

---

## 2. Architecture Review

### Layer Separation ✅

The lib/custom_components split is clean and enforced. S1 (no HA imports in lib/) is verified by mypy --strict and would immediately fail if violated. This is better than Shelly, which has tighter coupling to the HA framework throughout.

```
lib/tuya_cloudless/
  const.py       — wire constants
  crypto.py      — crypto ops only (S3 compliant)
  discovery.py   — UDP listener
  exceptions.py  — 16-type custom hierarchy
  message.py     — frame encode/decode, MessageBuffer
  profiles.py    — EntitySpec, DPSpec, YAML loader
  protocol.py    — TCP I/O (S2 compliant)

custom_components/tuya_cloudless/
  __init__.py    — entry setup, services, sys.path hack ⚠️
  coordinator.py — DataUpdateCoordinator, push mode
  config_flow.py — 4-step guided + manual + reauth + reconfigure
  entity.py      — base entity (get_dp, device_info, available)
  switch.py, light.py, sensor.py, binary_sensor.py
  cover.py, number.py, select.py, climate.py, fan.py
  diagnostics.py, repairs.py

profiles/        — 18 YAML device definitions
```

### Critical Flaw: `sys.path` Manipulation

**File:** `custom_components/tuya_cloudless/__init__.py`

The bundled library is made importable via `sys.path.insert(0, str(lib_path))`. This is the standard HACS pattern for bundled libs, but it has real risks:

1. **Name collision**: If any other HACS integration bundles a package named `tuya_cloudless`, they silently shadow each other.
2. **Reload safety**: On `async_unload_entry`, the path is NOT removed from `sys.path`. Accumulated paths across reloads.
3. **Import order**: Position 0 means it overrides *everything* in sys.path, including HA's own vendored packages.

**Recommended fix:** Add `__path__` manipulation or use a namespace package. Or better: convert to a proper Python package uploaded to PyPI and reference via `requirements:` in manifest.json.

### `_PROFILES_LOADED` Global Flag ⚠️

```python
_PROFILES_LOADED: bool = False

async def async_setup_entry(...):
    global _PROFILES_LOADED
    if not _PROFILES_LOADED:
        init_profiles(...)
        _PROFILES_LOADED = True
```

If two config entries load concurrently (two devices), there is a race between the `if not _PROFILES_LOADED` check and `_PROFILES_LOADED = True`. Python's GIL makes this unlikely to corrupt, but `init_profiles()` could be called twice. Solution: use a per-`hass` data key with an asyncio.Lock.

---

## 3. Runtime Robustness Review

### Exponential Backoff ✅

Coordinator reconnect uses exponential backoff with a 60s cap. Well-implemented.

### Heartbeat Loop ✅

20-second heartbeat with timeout. Properly cancels the heartbeat task on disconnect.

### Session Key Re-negotiation on Reconnect ✅

Protocol v3.4/3.5 re-runs ECDH negotiation on every new TCP connection. Session keys are not persisted across reconnects, which is correct.

### MessageBuffer Memory Safety ✅

256 KB hard limit prevents memory exhaustion from malformed streams. Raises `ProtocolError` on overflow.

### Consecutive Decode Error Repair Issue ✅

Coordinator creates a repair issue after 3 consecutive decode errors. Good defensive pattern.

### Command Constant Conflict ⚠️

**File:** `lib/tuya_cloudless/const.py` lines 63–78

```python
CMD_BIND_DEVICE: int = 0x03         # ← same value as...
CMD_SESS_KEY_NEG_START: int = 0x03  # ← this!
```

Both are assigned `0x03`. Meanwhile, `message.py` `CommandType` assigns:
- `ACTIVE = 0x03`
- `SESS_KEY_NEG = 0x04`
- `SESS_KEY_NEG_RESP = 0x05`

So `const.py CMD_SESS_KEY_NEG_START = 0x03` conflicts with `CommandType.ACTIVE = 0x03`, and `CMD_SESS_KEY_NEG_RESPONSE = 0x04` conflicts with `CommandType.SESS_KEY_NEG = 0x04`. If `protocol.py` uses `const.py` values while `message.py` uses its own `CommandType` enum, session key negotiation packets will be mis-identified as ACTIVE/BIND commands.

**Severity: HIGH** — potential protocol failure for v3.4/3.5 devices.

### `asyncio.get_event_loop()` Deprecation ⚠️

**File:** `lib/tuya_cloudless/discovery.py` line 256

```python
deadline = asyncio.get_event_loop().time() + timeout
```

`asyncio.get_event_loop()` is deprecated in Python 3.10+ and raises `DeprecationWarning`. Should be `asyncio.get_running_loop()`.

### `devices()` Iterator Semantic Bug ⚠️

**File:** `lib/tuya_cloudless/discovery.py` lines 268–285

```python
async def devices(self) -> AsyncIterator[DiscoveredDevice]:
    while self._running:
        self._device_event.clear()
        yield_batch = list(self._discovered.values())
        for dev in yield_batch:
            yield dev                    # yields ALL known devices
        if not yield_batch:
            await asyncio.sleep(DISCOVERY_POLLING_INTERVAL)
            continue
        await self._device_event.wait()  # then waits for any change
```

On every `_device_event` signal (which fires even for device _updates_, not just new devices), it re-yields the entire known device dict. A caller iterating over `devices()` would receive duplicates on every device update. The intent is "yield new devices," but the implementation is "yield all known devices whenever anything changes."

---

## 4. HA Integration Quality Review

### Config Flow ✅

Four-step guided flow (scan → select → local_key → confirm) + manual + reauth + reconfigure + options is excellent. Better than most Silver-tier integrations.

### Repair Flows ❌

**File:** `custom_components/tuya_cloudless/repairs.py`

Both repair flows are empty `ConfirmRepairFlow`:

```python
class TuyaAuthRepairFlow(RepairsFlow):
    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm")
```

The auth repair flow does **not** redirect the user to reauth. It just closes the repair issue. This means when a local_key rotation occurs, the user sees a repair issue, confirms it, and... nothing happens. The device stays broken.

**Fix:** Auth repair should call `entry.async_start_reauth()` to trigger the reauth flow.

### Device Info: Unhelpful Names ⚠️

**File:** `custom_components/tuya_cloudless/entity.py` lines 52–62

```python
return DeviceInfo(
    identifiers={(DOMAIN, gw_id)},
    name=gw_id,                    # ← "abc123def456" — terrible UX
    manufacturer="Tuya",
    model="Tuya Cloudless",        # ← all devices show same model
    sw_version=self.coordinator._version,
)
```

Every device in HA will be named with its raw gateway ID (a 22-char hex string) and show "Tuya Cloudless" as the model. After integration of 18 profiles with specific device types, this is a missed opportunity. The profile name (e.g. "Smart Thermostat") and a user-provided friendly name should be used.

The `profile_name` is available in `TuyaCloudlessRuntimeData.profile_name` but is not used in `device_info`.

### Private Attributes Leaked ⚠️

`coordinator._gw_id` and `coordinator._version` are private (leading underscore) but accessed from `entity.py`, `config_flow.py`, and tests. These should be public properties.

### `EntityCategory` Coverage ✅

Diagnostic sensors use `EntityCategory.DIAGNOSTIC` with `entity_registry_enabled_default = False`. Correctly follows HA convention.

### Translation Coverage ✅

strings.json, en.json, sv.json updated for all 9 platforms. Icons defined in icons.json for all entity keys.

### `extra_state_attributes` Incomplete for Multi-DP Entities ⚠️

**File:** `custom_components/tuya_cloudless/entity.py` lines 79–86

The base `extra_state_attributes` only exposes `_dp_id` (the primary DP). Climate entities have 4 DPs (power, mode, temp_set, temp_current). Fan entities have up to 5 DPs. Users debugging these entities will only see one raw value.

---

## 5. Code Quality and Maintainability Review

### Scale Convention Inconsistency — Critical Authoring Trap ⚠️

The scale field in DPSpec has **opposite meanings** in different platform files:

| Platform | Convention | Example |
|----------|-----------|---------|
| sensor.py | `display = raw * scale` | raw=222, scale=0.1 → 22.2°C |
| number.py | `display = raw * scale` | raw=250, scale=0.1 → 25.0 |
| climate.py | `raw = temp * scale` | temp=22.0, scale=10 → 220 |

A YAML profile author writing a climate entity must use scale=10 to get 22.0°C from raw 220, but a sensor on the same device must use scale=0.1 for the same conversion. This is a documentation landmine that **will** cause incorrect profile YAML files in the field.

**Root cause:** The `climate.py` AC follows the plan spec which specified scale=10 for the thermostat, while sensor/number follow the standard convention. The convention should be unified. Recommended: always `display = raw * scale`, which means climate.py should be `raw = round(temp / scale)` and scale in profiles should be 0.1 (not 10).

### `binary_sensor.py` Anti-Pattern ⚠️

**File:** `custom_components/tuya_cloudless/binary_sensor.py` line 76

```python
with __import__("contextlib").suppress(ValueError):
```

This bypasses linter import checks. The correct pattern (used correctly in `cover.py`) is:

```python
import contextlib
...
with contextlib.suppress(ValueError):
```

### Type Safety: `_spec` Attributes Untyped in Base ⚠️

Each platform class stores `self._spec = spec`, but `TuyaCloudlessEntity` has no `_spec` attribute. If a subclass property tries to access `self._spec` without the `__init__` having run (e.g., in tests using `__new__`), mypy allows it because each class declares its own `_spec`. This is fragile — a mismatched spec type (e.g., passing a sensor spec to a climate entity) would be a runtime error, not a type error.

### `coordinator._gw_id` Underscore Convention ⚠️

Used across the codebase but defined as a "private" attribute. It's effectively a public API. Should be a `@property`:

```python
@property
def gw_id(self) -> str:
    return self._gw_id
```

### `int(value)` / `str(value)` Defensive Casts ✅

All DP accessor properties cast to the expected type (`int(raw)`, `bool(value)`, `str(value)`) before returning. This correctly handles Tuya devices that sometimes return stringified integers.

---

## 6. Protocol and Transport Design Review

### Message Layer ✅ (message.py)

`MessageBuffer` is the highlight of the protocol implementation:
- Handles TCP stream fragmentation correctly
- 256 KB hard cap against memory exhaustion
- `_find_prefix()` correctly discards garbage before the 0x55AA magic
- Partial prefix at buffer boundary is preserved (last 3 bytes kept)
- HMAC-SHA256 uses `hmac.compare_digest` for constant-time comparison

### Crypto Layer ✅ (crypto.py)

- AES-ECB with PKCS7 padding for v3.1–3.3 (correct per Tuya spec)
- AES-GCM with ECDH X25519 for v3.4/3.5 (correct)
- `cryptography` (PyCA) library only — `pycryptodome` removed
- GCM nonce(12)+tag(16)+ciphertext wire format handled correctly
- MD5 key derivation for ECB versions correct

### Discovery Layer ✅ (discovery.py)

- Binds to both UDP 6666 (plain) and 6667 (encrypted)
- Queue-based decoupling from UDP socket (`asyncio.Queue(maxsize=256)`)
- Drops on queue full (correct — UDP is lossy anyway)
- `_try_decode_payload` correctly tries plain JSON first, then decrypts with each known key
- `DiscoveredDevice.is_stale()` staleness detection present

### Wire Constants Duplication ⚠️

Command codes are defined in **both** `lib/tuya_cloudless/const.py` and `lib/tuya_cloudless/message.py` (as `CommandType` enum). The two definitions have conflicting values for session key negotiation commands. This dual-source-of-truth is risky. `protocol.py` should use only `CommandType` from `message.py`.

---

## 7. Entity Model and State Model Review

### Profile-Driven Architecture ✅

The YAML profile → EntitySpec → HA entity pipeline is the correct abstraction. It cleanly separates device knowledge from code. 18 profiles cover ~70% of common Tuya device types.

### EntitySpec Immutability ✅

`EntitySpec` and `DPSpec` are `frozen=True` dataclasses. Correct. `TuyaCloudlessRuntimeData` is also frozen.

### Cover Entity Missing STOP ⚠️

**File:** `custom_components/tuya_cloudless/cover.py`

`CoverEntityFeature.STOP` is not implemented despite being in `CoverEntityFeature`. Virtually all Tuya blind/curtain motors support a stop command (usually a second control DP or a specific enum value). Without STOP, HA shows open/close but no way to halt mid-movement.

### Climate Scale Inverted ⚠️

As noted in §5. The `async_set_temperature` method in climate.py uses `raw = round(temp * scale)` while number.py uses `raw = round(value / scale)`. For a device where `raw=220 → 22.0°C`, sensor.py would need `scale=0.1` but climate.py would need `scale=10`. Both profile definitions are included in the 18 profiles and use the climate convention, but this creates confusion.

### Fan `percentage` is Raw DP Value ⚠️

**File:** `custom_components/tuya_cloudless/fan.py` lines 99–106

```python
@property
def percentage(self) -> int | None:
    ...
    return int(value)
```

The HA `FanEntity.percentage` contract expects a value from 0–100 representing a percentage. But Tuya fan speed DPs are typically 1–6 (discrete speed levels). Returning raw DP value 4 as "4%" is misleading. Should use `percentage_value` range mapping or `speed_count` instead.

### `DeviceState` is a Frozen Dataclass ✅

The coordinator state is immutable — a new `DeviceState` is created on each update. This prevents partial-state corruption and is correct.

---

## 8. Diagnostics, Logging, and Supportability Review

### Diagnostics ✅

`diagnostics.py` returns a three-section dict (config, state, connection) with no secrets. Correctly redacts `local_key` (shown as length, not value). Meets Gold tier.

### Logging Quality ✅

All log messages include `[{gw_id}]` prefix for easy filtering. Protocol version and connection state transitions are logged at appropriate levels.

### No Secret Leakage ✅

RULE 0 from CLAUDE.md is observed throughout. Keys are never logged, never included in exceptions. Exception messages show lengths only.

### Repair Issues: Content-Free ❌

As noted in §4 — repair issues are created but the repair flows do nothing. A user seeing "Authentication failure" in HA's repair dashboard who confirms it will still have a broken device.

### `extra_state_attributes` Coverage ⚠️

Only the primary `_dp_id` and its raw value are exposed. For climate/fan with 4-5 DPs, this is insufficient for user debugging.

---

## 9. Test Strategy Review

### Coverage ✅

815 tests, 97% coverage across all modules. Exceeds the Silver tier requirement.

### Test Pattern: `__new__` Bypass ⚠️

The standard test helper pattern across all test files:

```python
entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
entity.coordinator = coord
entity._spec = spec
# manually set all _attr_* values
```

This **bypasses `__init__`**. If `__init__` has a bug (wrong attribute set, missing validation), these unit tests will not catch it. The `TestClimateSetupEntry.test_setup_creates_entities` test does exercise `__init__` through `async_setup_entry`, but it only checks entity count, not attribute values.

**Fix:** Add `TestClimateInit` tests that construct via `TuyaCloudlessClimate(coordinator, spec)` and verify `_attr_*` values were set correctly.

### Protocol Tests ✅

`tests/unit/test_message.py` and `tests/unit/test_crypto.py` test actual wire format round-trips including edge cases. This is rare for custom integrations.

### Integration Tests: None

There are no integration tests that run against a mock TCP server. Shelly has integration tests that spin up a fake Shelly device. A mock Tuya device server would catch protocol regressions that unit tests miss.

---

## 10. UX Consequences of Code Decisions

| Decision | UX Consequence |
|----------|---------------|
| `device_info.name = gw_id` | Device shows as "abc123def456" in HA UI — incomprehensible |
| `device_info.model = "Tuya Cloudless"` | All devices look identical in registry — can't distinguish device types |
| Climate scale=10 vs sensor scale=0.1 | Profile YAML authors will set wrong scale and get 10x errors |
| Fan `percentage` returns raw DP (1-6) | Fans show 4% speed instead of "speed 4/6" |
| Auth repair flow does nothing | User sees repair issue, confirms it, device still broken — confusion |
| No STOP on cover | Users can't halt blinds mid-movement — a common real-world need |
| `_PROFILES_LOADED` global | Profiles only loaded on first entry setup — second entry may not see new profiles if added between setups |
| Scale convention inversion | Thermostat profile uses scale=10 but sensor on same device needs scale=0.1 |

---

## 11. Side-by-Side: Tuya Cloudless vs Shelly

| Criterion | Tuya Cloudless | Shelly |
|-----------|---------------|--------|
| **Layer separation** | ✅ Hard boundary lib/HA | ⚠️ Mixed in places |
| **Protocol coverage** | ✅ v3.1–3.5 (AES-ECB + AES-GCM) | N/A (HTTP/CoAP/MQTT) |
| **Config flow** | ✅ 4-step guided + all HA flows | ✅ Discovery-first + all flows |
| **Device naming** | ❌ gw_id hex string | ✅ User-visible model name |
| **Profile system** | ✅ YAML-driven | ❌ Hard-coded per-model classes |
| **Push mode** | ✅ TCP push, no poll | ✅ CoAP/MQTT push |
| **Test coverage** | ✅ 815 tests, 97% | ✅ High coverage |
| **Integration tests** | ❌ None (unit only) | ✅ Mock device server tests |
| **Repair flows** | ❌ Empty confirms only | ✅ Guided remediation |
| **Type annotations** | ✅ mypy --strict | ✅ mypy --strict |
| **Error hierarchy** | ✅ 16 typed exceptions | ✅ Custom hierarchy |
| **HMAC constant-time** | ✅ hmac.compare_digest | ✅ standard practice |
| **Scale convention** | ❌ Inconsistent (climate vs others) | N/A |
| **Scale constant defs** | ❌ Duplicate + conflicting | ✅ Single source |
| **Cover STOP** | ❌ Missing | ✅ Implemented |
| **Fan percentage mapping** | ❌ Raw DP value | ✅ Proper 0-100 mapping |
| **Diagnostics** | ✅ Secrets redacted | ✅ Secrets redacted |
| **Deprecation warnings** | ⚠️ get_event_loop() | ✅ Clean |
| **HACS bundled lib** | ⚠️ sys.path hack | N/A (PyPI package) |
| **Quality scale** | ✅ Gold tier self-assessed | ✅ Official integration |

**Summary:** Tuya Cloudless leads Shelly in profile flexibility and library separation, but lags in UX polish, repair flows, and a handful of correctness issues.

---

## 12. Top 20 Issues Blocking 10/10

Ranked by severity × user-visibility:

| # | Issue | Severity | File | Fix |
|---|-------|----------|------|-----|
| 1 | Device name = raw gw_id hex | HIGH | entity.py:56 | Use `profile_name` from runtime_data |
| 2 | CMD constant conflict (0x03 double-assigned) | HIGH | const.py:63,77 | Remove const.py CMD_ aliases for session key; use CommandType only |
| 3 | Scale convention inverted: climate vs number/sensor | HIGH | climate.py vs number.py | Standardize: always `display = raw * scale`; update climate.py and profiles |
| 4 | Auth repair flow does nothing | HIGH | repairs.py:15 | Call `entry.async_start_reauth()` from repair |
| 5 | sys.path insertion not cleaned up on unload | MEDIUM | __init__.py | Remove path on unload; long-term: publish lib to PyPI |
| 6 | Fan percentage is raw DP, not 0-100% | MEDIUM | fan.py:106 | Map raw range to 0-100 using dp_value.min_raw/max_raw |
| 7 | Cover missing STOP feature | MEDIUM | cover.py | Add dp_stop DPSpec to EntitySpec + CoverEntityFeature.STOP |
| 8 | `_PROFILES_LOADED` global race | MEDIUM | __init__.py | Use hass.data lock or per-entry init |
| 9 | device_info.model hardcoded "Tuya Cloudless" | MEDIUM | entity.py:60 | Use profile_name as model; set friendly name from config |
| 10 | `asyncio.get_event_loop()` deprecated | MEDIUM | discovery.py:256 | Replace with `asyncio.get_running_loop()` |
| 11 | `devices()` re-yields all known on each event | MEDIUM | discovery.py:268 | Track yielded device IDs; only yield new/changed |
| 12 | `coordinator._gw_id` accessed as private | LOW | entity.py:55 | Add `@property gw_id` and `version` on coordinator |
| 13 | `__import__("contextlib")` anti-pattern | LOW | binary_sensor.py:76 | Normal `import contextlib` at top of file |
| 14 | `extra_state_attributes` only exposes primary DP | LOW | entity.py:79 | Override in multi-DP entities to show all DP values |
| 15 | Test `__new__` bypass skips `__init__` validation | LOW | test_ha_*.py | Add explicit `__init__` construction tests |
| 16 | No integration tests (mock TCP server) | LOW | tests/ | Add tests/integration/ with mock Tuya TCP server |
| 17 | `TuyaRuntimeData.entity_specs: list` is mutable despite frozen | LOW | __init__.py | Use `tuple[EntitySpec, ...]` |
| 18 | Repair connectivity flow provides no diagnostic info | LOW | repairs.py | Show last seen time, retry count, IP in repair form |
| 19 | `wait_for_device()` raises `TimeoutError` but type hint says `asyncio.TimeoutError` | LOW | discovery.py:263 | Use `asyncio.TimeoutError` consistently |
| 20 | Climate `hvac_mode` fallback to HEAT when no mode DP | LOW | climate.py | Return `HVACMode.AUTO` as fallback (more neutral) |

---

## 13. Refactor Roadmap

### Phase 1 — Correctness & Safety (Sprint 1, 1 week)

These fix bugs or regressions, should be done before any public release:

1. **Fix CMD constant conflict** in `const.py` (issue #2)
2. **Fix scale convention** in `climate.py` — change to `raw = round(temp / scale)`, update 4 climate profiles (issue #3)
3. **Fix `asyncio.get_event_loop()`** → `get_running_loop()` (issue #10)
4. **Fix `devices()` re-yield bug** (issue #11)
5. **Fix `binary_sensor.py` `__import__` anti-pattern** (issue #13)
6. **Change `entity_specs` to `tuple`** in runtime data (issue #17)

### Phase 2 — UX & Completeness (Sprint 2, 1 week)

7. **Device naming** — use `profile_name` as `DeviceInfo.model`, allow user-set friendly name in config flow (issues #1, #9)
8. **Auth repair flow** — trigger reauth from repair (issue #4)
9. **Fan percentage mapping** — map raw 1–6 → 0–100% using DPSpec ranges (issue #6)
10. **Cover STOP** — add `dp_stop` to EntitySpec + implement in cover.py (issue #7)
11. **`extra_state_attributes`** — expose all DPs for multi-DP entities (issue #14)
12. **Repair connectivity** — show diagnostic info in repair form (issue #18)

### Phase 3 — Architecture & Maintainability (Sprint 3, 2 weeks)

13. **`sys.path` bundling** — publish `tuya_cloudless` lib to PyPI, add to `requirements:` in manifest.json; remove `sys.path` hack (issue #5)
14. **`_PROFILES_LOADED` global** — per-hass initialization with asyncio.Lock (issue #8)
15. **`coordinator._gw_id` → property** (issue #12)
16. **Integration test suite** — mock TCP server covering v3.3 and v3.4 handshake + DP push (issue #16)
17. **Test `__init__` coverage** — add construction tests in addition to `__new__` bypass tests (issue #15)

---

## 14. GitHub-Ready Issues

### Epic: PLAT-700 — Protocol Correctness

**PLAT-701: Fix CMD constant conflict in const.py**
- Remove duplicate session key negotiation constants from `const.py`
- Ensure `protocol.py` uses `CommandType` enum from `message.py` exclusively
- Add regression test that verifies v3.4 session key command codes
- Labels: `bug`, `protocol`, `priority:high`

**PLAT-702: Standardize scale convention (display = raw × scale)**
- Update `climate.py` to use `raw = round(temp / scale)` and `temp = raw * scale`
- Update `electric_heater.yaml`, `thermostat.yaml`, `ac_cool_heat.yaml`, `floor_heating.yaml` scale values (10 → 0.1)
- Add scale convention documentation to DOMAIN.md
- Labels: `bug`, `breaking-change`, `profiles`

**PLAT-703: Fix asyncio.get_event_loop() deprecation**
- Replace with `asyncio.get_running_loop()` in `discovery.py:256`
- Labels: `bug`, `deprecation`

**PLAT-704: Fix DiscoveryListener.devices() re-yield bug**
- Track yielded device IDs; yield only new or changed devices
- Add test: listener with 5 known devices yields exactly 1 new device per `_device_event.set()`
- Labels: `bug`, `discovery`

---

### Epic: PLAT-710 — UX & Device Registry

**PLAT-711: Use profile name in DeviceInfo**
- Pass `profile_name` from `TuyaCloudlessRuntimeData` to entity base
- Set `DeviceInfo.name` to user-provided name (or profile_name fallback)
- Set `DeviceInfo.model` to `profile_name`
- Labels: `enhancement`, `ux`, `priority:high`

**PLAT-712: Auth repair flow → trigger reauth**
- `TuyaAuthRepairFlow.async_step_confirm` should call `entry.async_start_reauth()` after confirm
- Add test: repair flow confirmed → reauth flow initiated
- Labels: `bug`, `repair`, `priority:high`

**PLAT-713: Fan percentage range mapping**
- Use `dp_value.min_raw`/`max_raw` to map raw 1–6 → 0–100%
- Update `ceiling_fan.yaml`, `ventilation.yaml` to set `min_raw`/`max_raw` correctly
- Labels: `bug`, `fan`

**PLAT-714: Cover STOP support**
- Add `dp_stop: DPSpec | None = None` to `EntitySpec`
- Add `async_stop_cover` to `TuyaCloudlessCover`
- Add `dp_stop` to `ceiling_fan.yaml` cover spec
- Labels: `enhancement`, `cover`

---

### Epic: PLAT-720 — Code Hygiene

**PLAT-721: Fix binary_sensor __import__ anti-pattern**
- Move `import contextlib` to top of `binary_sensor.py`
- Labels: `cleanup`

**PLAT-722: Add coordinator gw_id/version properties**
- Expose `TuyaCloudlessCoordinator.gw_id` and `.version` as `@property`
- Update all references from `._gw_id`/`._version` to `.gw_id`/`.version`
- Labels: `cleanup`, `type-safety`

**PLAT-723: Fix _PROFILES_LOADED global race**
- Use `hass.data[DOMAIN]["profiles_loaded"]` with `asyncio.Lock` for thread-safe one-time init
- Labels: `bug`, `concurrency`

**PLAT-724: Freeze entity_specs in runtime data**
- Change `entity_specs: list[EntitySpec]` to `entity_specs: tuple[EntitySpec, ...]`
- Labels: `cleanup`, `type-safety`

---

### Epic: PLAT-730 — Testing

**PLAT-731: Add __init__ construction tests**
- For each platform: `TestXxxInit` that constructs entity via `TuyaCloudlessXxx(coordinator, spec)` and asserts attribute values
- Labels: `test`, `coverage`

**PLAT-732: Integration test suite with mock Tuya TCP server**
- `tests/integration/mock_device.py` — minimal v3.3 and v3.4 Tuya TCP server
- Tests: connect, DP push, reconnect after drop, auth error
- Labels: `test`, `integration`

---

*End of review. Issues #1-#20 above map to PLAT-701 through PLAT-732.*
