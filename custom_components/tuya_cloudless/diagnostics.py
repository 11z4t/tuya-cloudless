"""Diagnostics platform for Tuya Cloudless.

Provides an exportable JSON snapshot of a device's runtime state for
troubleshooting. Accessible via Home Assistant → Settings → Devices →
select device → Download diagnostics.

No secret material (keys, tokens) is included in the export.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


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

    return {
        "config": {
            "gw_id": entry.data.get(CONF_GW_ID, ""),
            "ip_address": entry.data.get(CONF_IP_ADDRESS, ""),
            "protocol_version": entry.data.get(CONF_PROTOCOL_VERSION, ""),
            "profile": entry.data.get(CONF_PROFILE, runtime.profile_name),
        },
        "state": {
            "available": coord.state.available,
            "last_seen": (coord.state.last_seen.isoformat() if coord.state.last_seen else None),
            "reconnect_count": coord.state.reconnect_count,
            "last_error": coord.state.last_error,
            "dps": dict(coord.state.dps),
        },
        "connection": {
            "tcp_connected": coord._writer is not None,
            "sequence_counter": coord._sequence,
            "session_key_active": coord._session_key is not None,
            "consecutive_decode_errors": coord._consecutive_decode_errors,
        },
    }
