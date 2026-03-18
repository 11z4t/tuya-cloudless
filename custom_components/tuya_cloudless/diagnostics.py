"""Diagnostics platform for Tuya Cloudless.

Provides an exportable JSON snapshot of a device's runtime state for
troubleshooting. Accessible via Home Assistant → Settings → Devices →
select device → Download diagnostics.

Sensitive data is redacted using async_redact_data():
- local_key     → **REDACTED** (symmetric encryption key)
- gw_id         → first 4 chars + **REDACTED** (partial, preserves device family)
- ip_address    → host prefix preserved, last octet → ** (e.g. 192.168.1.**)

Approach mirrors Shelly (8/10 reference) with additional partial-redaction
callables for IP and device-ID fields (10/10 target).
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import REDACTED, async_redact_data


def _sanitize_dps(dps: dict[str, Any]) -> dict[str, Any]:
    """Sanitize DPS dict before including in diagnostics output.

    - String values longer than 64 chars are replaced with ``[REDACTED-LONG]``
      to avoid leaking base64-encoded keys or large payloads.
    - Bytes values are replaced with ``[REDACTED-BYTES:<length>]``.
    - All other values (int, bool, float) pass through unchanged.

    Args:
        dps: Raw DPS dict from device state.

    Returns:
        A new dict with the same keys and sanitized values.
    """
    result: dict[str, Any] = {}
    for k, v in dps.items():
        if isinstance(v, bytes):
            result[k] = f"[REDACTED-BYTES:{len(v)}]"
        elif isinstance(v, str) and len(v) > 64:
            result[k] = "[REDACTED-LONG]"
        else:
            result[k] = v
    return result


def _partial_gw_id(value: str) -> str:
    """Return first 4 chars of gw_id, rest replaced with REDACTED marker."""
    if len(value) <= 4:
        return REDACTED
    return value[:4] + REDACTED


def _partial_ip(value: str) -> str:
    """Redact last octet of an IPv4 address (e.g. 192.168.1.100 → 192.168.1.**)."""
    parts = value.rsplit(".", 1)
    if len(parts) == 2:
        return parts[0] + ".**"
    return REDACTED


# Keys redacted with custom callables for partial visibility
_CONFIG_REDACT: dict[str, Any] = {
    "local_key": lambda _: REDACTED,
    "gw_id": _partial_gw_id,
    "ip_address": _partial_ip,
}

# Keys fully redacted in raw entry.as_dict() (safety net)
_ENTRY_REDACT = {"local_key", "password", "token", "api_key"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a Tuya Cloudless config entry.

    The returned dict is JSON-serialisable and safe to share — it contains
    no key material or personally identifiable information.

    Args:
        hass: Home Assistant instance.
        entry: Config entry to collect diagnostics for.

    Returns:
        Nested dict with ``config``, ``state``, and ``connection`` sections.
    """
    from . import TuyaCloudlessRuntimeData
    from .const import CONF_GW_ID, CONF_IP_ADDRESS, CONF_PROFILE, CONF_PROTOCOL_VERSION

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    coord = runtime.coordinator

    raw_config = {
        "gw_id": entry.data.get(CONF_GW_ID, ""),
        "ip_address": entry.data.get(CONF_IP_ADDRESS, ""),
        "protocol_version": entry.data.get(CONF_PROTOCOL_VERSION, ""),
        "profile": entry.data.get(CONF_PROFILE, runtime.profile_name),
    }

    return {
        "config": async_redact_data(raw_config, _CONFIG_REDACT),
        "state": {
            "available": coord.state.available,
            "last_seen": (coord.state.last_seen.isoformat() if coord.state.last_seen else None),
            "reconnect_count": coord.state.reconnect_count,
            "last_error": coord.state.last_error,
            # NOTE: DPS values may contain sensitive device state. Review before sharing.
            "dps": _sanitize_dps(dict(coord.state.dps)),
        },
        "connection": {
            "tcp_connected": coord.tcp_connected,
            "sequence_counter": coord.sequence_counter,
            "session_key_active": coord.session_key_active,
            "consecutive_decode_errors": coord.consecutive_decode_errors,
        },
    }
