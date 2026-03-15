"""Tuya Cloudless integration for Home Assistant.

Local-only control of Tuya WiFi devices using:
  - Web Bluetooth for initial pairing (no cloud account)
  - Fake-cloud local activation mock
  - TCP port 6668 for runtime control (push updates)
  - UDP 6666/6667 for device discovery

Compatible with Tuya LAN protocol versions 3.1-3.5.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure bundled lib/tuya_cloudless is importable both in production (lib/ symlinked
# inside custom_components) and in development (lib/ at repo root).
_BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
_DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
    if Path(_lib_dir).is_dir() and _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
        break

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_GW_ID, DOMAIN, PLATFORMS
from .coordinator import TuyaCloudlessCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class TuyaCloudlessRuntimeData:
    """Typed runtime data attached to each config entry."""

    coordinator: TuyaCloudlessCoordinator
    device_info: DeviceInfo


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Tuya Cloudless device from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry.

    Returns:
        True if setup succeeded.
    """
    _LOGGER.info("Setting up Tuya Cloudless entry: %s", entry.title)

    coordinator = TuyaCloudlessCoordinator.from_config_entry(hass, entry)

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.data[CONF_GW_ID])},
        name=entry.title,
        manufacturer="Tuya",
        model=f"Tuya Cloudless ({entry.data.get('protocol_version', '3.3')})",
    )

    entry.runtime_data = TuyaCloudlessRuntimeData(
        coordinator=coordinator,
        device_info=device_info,
    )

    await coordinator.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Tuya Cloudless entry set up: %s", entry.title)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Tuya Cloudless config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry to unload.

    Returns:
        True if unloaded successfully.
    """
    _LOGGER.info("Unloading Tuya Cloudless entry: %s", entry.title)

    runtime: TuyaCloudlessRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        await runtime.coordinator.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    _LOGGER.info("Tuya Cloudless entry unloaded: %s (ok=%s)", entry.title, unload_ok)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to latest version."""
    if entry.version > 1:
        return False
    _LOGGER.debug("No migration needed for entry %s (v%s)", entry.entry_id, entry.version)
    return True
