"""Tuya Cloudless integration for Home Assistant.

Local-only control of Tuya WiFi devices using:
  - Web Bluetooth for initial pairing (no cloud account)
  - Fake-cloud local activation mock
  - TCP port 6668 for runtime control (push updates)
  - UDP 6666/6667 for device discovery

Compatible with Tuya LAN protocol versions 3.1-3.5.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

# Ensure bundled lib/tuya_cloudless is importable both in production (lib/ symlinked
# inside custom_components) and in development (lib/ at repo root).
# Track the inserted path so we can clean it up when the last entry unloads.
# Idempotent: we check sys.path before inserting so HA reloads do not accumulate
# duplicate entries (PLAT-712).
_INSERTED_LIB_PATH: str | None = None
_BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
_DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
    if Path(_lib_dir).is_dir():
        if _lib_dir not in sys.path:
            sys.path.insert(0, _lib_dir)
            _INSERTED_LIB_PATH = _lib_dir
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
    CONF_IP_ADDRESS,
    CONF_PROFILE,
    CONF_PROTOCOL_VERSION,
    DEVICE_TYPE_TO_PROFILE,
    DOMAIN,
    PLATFORMS,
    PROFILES_DIR,
)
from .coordinator import TuyaCloudlessCoordinator

_LOGGER = logging.getLogger(__name__)

# Per-hass domain data keys
_KEY_PROFILES_LOADED = "profiles_loaded"
_KEY_PROFILES_LOCK = "profiles_lock"


async def _ensure_profiles(hass: HomeAssistant) -> None:
    """Load device profiles from disk if not already loaded for this HA instance.

    Uses a per-hass asyncio.Lock to prevent duplicate loading when multiple
    config entries set up concurrently. Disk I/O runs in the executor thread pool.
    """
    domain_data: dict[str, object] = hass.data.setdefault(DOMAIN, {})

    # Fast path: already loaded
    if domain_data.get(_KEY_PROFILES_LOADED):
        return

    # Ensure lock exists (only one coroutine reaches this per HA instance)
    if _KEY_PROFILES_LOCK not in domain_data:
        domain_data[_KEY_PROFILES_LOCK] = asyncio.Lock()

    async with domain_data[_KEY_PROFILES_LOCK]:  # type: ignore[union-attr]
        if not domain_data.get(_KEY_PROFILES_LOADED):
            await hass.async_add_executor_job(init_profiles, PROFILES_DIR)
            domain_data[_KEY_PROFILES_LOADED] = True


async def _resolve_profile(hass: HomeAssistant, entry: ConfigEntry) -> DeviceProfile | None:
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
    await _ensure_profiles(hass)

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

    Device metadata lives on ``coordinator.device_info`` — the single
    canonical source (PLAT-771).  Access it via ``entry.runtime_data.coordinator.device_info``
    rather than storing a second copy here.
    """

    coordinator: TuyaCloudlessCoordinator
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

    # Resolve profile before creating the coordinator so that device_info
    # can be built once and passed in — making it the single canonical source
    # (PLAT-771).  The coordinator stores it as a proper typed attribute;
    # all entities read it via TuyaCloudlessEntity.device_info.
    profile = await _resolve_profile(hass, entry)
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

    # Build DeviceInfo exactly once and pass it to the coordinator (PLAT-771).
    protocol_version: str = entry.data.get(CONF_PROTOCOL_VERSION, "3.3")
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.data[CONF_GW_ID])},
        name=entry.title,
        manufacturer="Tuya",
        model=profile_name or "Tuya Cloudless",
        sw_version=protocol_version,
        configuration_url=f"http://{entry.data.get(CONF_IP_ADDRESS, '')}",
    )

    coordinator = TuyaCloudlessCoordinator.from_config_entry(hass, entry, device_info=device_info)
    coordinator.device_name = entry.title
    coordinator.profile_name = profile_name

    entry.runtime_data = TuyaCloudlessRuntimeData(
        coordinator=coordinator,
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
            # coordinator already wraps errors — let them propagate as-is
            raise
        except (OSError, TimeoutError) as exc:
            # Low-level network errors not yet wrapped by the coordinator
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

    # Clean up sys.path when the last entry for this domain is unloaded.
    # The inserted path was only needed for the initial import; it is safe to
    # remove once no more entries are active.
    global _INSERTED_LIB_PATH
    remaining = hass.config_entries.async_entries(DOMAIN)
    if len(remaining) <= 1 and _INSERTED_LIB_PATH is not None:
        with contextlib.suppress(ValueError):
            sys.path.remove(_INSERTED_LIB_PATH)
        _INSERTED_LIB_PATH = None

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
    """Migrate config entry to the current schema version.

    Versions:
        1 -> 2: Move ``ip_address`` and ``protocol_version`` from
               ``entry.options`` (if present there) into ``entry.data`` where
               they exclusively belong.  The options flow no longer exposes
               these fields (PLAT-715).

    Args:
        hass: Home Assistant instance.
        entry: Config entry to migrate.

    Returns:
        True if the migration succeeded (or was a no-op), False if the
        entry version is newer than this code knows about.
    """
    current_version: int = entry.version

    if current_version > 2:
        _LOGGER.error(
            "Cannot migrate config entry %s from version %d -- unknown version",
            entry.entry_id,
            current_version,
        )
        return False

    if current_version == 1:
        _LOGGER.info(
            "Migrating config entry %s from v1 to v2 (options -> data boundary fix)",
            entry.entry_id,
        )
        options: dict[str, object] = dict(entry.options or {})
        new_data: dict[str, object] = dict(entry.data)

        # Move ip_address and protocol_version out of options into data.
        # They were never supposed to be in options; this cleans up any
        # entries that were inadvertently written there.
        migrated_fields: list[str] = []
        for field_key in (CONF_IP_ADDRESS, CONF_PROTOCOL_VERSION):
            if field_key in options:
                new_data.setdefault(field_key, options.pop(field_key))
                migrated_fields.append(field_key)

        if migrated_fields:
            _LOGGER.debug(
                "[%s] Moved fields from options -> data: %s",
                entry.entry_id,
                migrated_fields,
            )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=options,
            version=2,
        )

    _LOGGER.debug("Migration complete for entry %s (now v%s)", entry.entry_id, entry.version)
    return True
