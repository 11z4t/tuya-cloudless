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
from typing import TypeAlias

# Ensure bundled lib/tuya_cloudless is importable both in production (lib/ symlinked
# inside custom_components) and in development (lib/ at repo root).
_BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
_DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
    if Path(_lib_dir).is_dir() and _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
        break

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo

from tuya_cloudless.profiles import (
    DeviceProfile,
    EntitySpec,
    find_profile,
    init_profiles,
    list_profiles,
)

from .const import (
    CONF_DEVICE_TYPE,
    CONF_GW_ID,
    CONF_PROFILE,
    DEVICE_TYPE_TO_PROFILE,
    DOMAIN,
    PLATFORMS,
    PROFILES_DIR,
)
from .coordinator import TuyaCloudlessCoordinator

_LOGGER = logging.getLogger(__name__)

# Whether profile registry has been initialised for this HA instance
_PROFILES_LOADED = False


def _ensure_profiles() -> None:
    """Load device profiles from disk if not already loaded."""
    global _PROFILES_LOADED
    if not _PROFILES_LOADED:
        init_profiles(PROFILES_DIR)
        _PROFILES_LOADED = True


def _resolve_profile(entry: ConfigEntry) -> DeviceProfile | None:
    """Resolve the device profile for a config entry.

    Tries in order:
    1. ``entry.data["profile"]`` — name saved by the new config flow.
    2. ``entry.data["device_type"]`` — name from the legacy config flow, mapped
       via ``DEVICE_TYPE_TO_PROFILE``.
    3. First available generic profile as fallback.

    Args:
        entry: Config entry to resolve a profile for.

    Returns:
        Resolved :class:`~tuya_cloudless.profiles.DeviceProfile`, or ``None``
        if no profiles are loaded at all.
    """
    _ensure_profiles()

    # New entries: profile name stored directly
    profile_name: str | None = entry.data.get(CONF_PROFILE)

    # Legacy entries: device_type → profile name
    if not profile_name:
        device_type = entry.data.get(CONF_DEVICE_TYPE, "generic")
        profile_name = DEVICE_TYPE_TO_PROFILE.get(device_type, "Generic Switch")

    profile = find_profile(profile_name) if profile_name else None

    if profile is None:
        # Last resort: use the first loaded profile
        all_profiles = list_profiles()
        profile = all_profiles[0] if all_profiles else None
        if profile:
            _LOGGER.warning(
                "[%s] Profile '%s' not found — falling back to '%s'",
                entry.data.get(CONF_GW_ID, entry.entry_id),
                profile_name,
                profile.name,
            )

    return profile


@dataclass(frozen=True)
class TuyaCloudlessRuntimeData:
    """Typed runtime data attached to each config entry.

    Frozen to prevent accidental mutation after setup. All fields are set
    once during ``async_setup_entry`` and then treated as read-only.
    """

    coordinator: TuyaCloudlessCoordinator
    device_info: DeviceInfo
    entity_specs: tuple[EntitySpec, ...]
    profile_name: str


# Typed config entry alias — gives type-safe access to entry.runtime_data
TuyaCloudlessConfigEntry: TypeAlias = ConfigEntry[TuyaCloudlessRuntimeData]


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

    profile = _resolve_profile(entry)
    entity_specs = profile.entities if profile else []
    profile_name = profile.name if profile else ""

    if profile:
        _LOGGER.info(
            "[%s] Using profile '%s' — %d entities",
            entry.data[CONF_GW_ID],
            profile.name,
            len(entity_specs),
        )
    else:
        _LOGGER.warning(
            "[%s] No profile found — no entities will be created",
            entry.data[CONF_GW_ID],
        )

    entry.runtime_data = TuyaCloudlessRuntimeData(
        coordinator=coordinator,
        device_info=device_info,
        entity_specs=tuple(entity_specs),
        profile_name=profile_name,
    )

    await coordinator.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _register_services(hass)

    _LOGGER.info("Tuya Cloudless entry set up: %s", entry.title)
    return True


def _register_services(hass: HomeAssistant) -> None:
    """Register integration-level services (idempotent — safe to call multiple times)."""

    if hass.services.has_service(DOMAIN, "send_raw_dps"):
        return

    async def _handle_send_raw_dps(call: ServiceCall) -> None:
        """Handle the send_raw_dps service call."""
        entry_id: str = call.data["entry_id"]
        dps: dict[str, object] = call.data["dps"]

        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise ServiceValidationError(
                f"Config entry '{entry_id}' not found",
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
            )

        runtime: TuyaCloudlessRuntimeData | None = getattr(entry, "runtime_data", None)
        if runtime is None:
            raise ServiceValidationError(
                f"Entry '{entry_id}' is not loaded",
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
            )

        try:
            await runtime.coordinator.async_send_dps(dps)
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(
                f"Failed to send DPS to {entry_id}: {exc}",
                translation_domain=DOMAIN,
                translation_key="send_failed",
            ) from exc

    hass.services.async_register(DOMAIN, "send_raw_dps", _handle_send_raw_dps)


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


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: object,
) -> bool:
    """Allow the user to remove a device that is no longer present.

    Returns True unconditionally — if the coordinator has disconnected the device,
    HA can safely remove it from the device registry.
    """
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to latest version."""
    if entry.version > 1:
        return False
    _LOGGER.debug("No migration needed for entry %s (v%s)", entry.entry_id, entry.version)
    return True
