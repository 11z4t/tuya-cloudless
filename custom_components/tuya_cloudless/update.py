"""Update entity for Tuya Cloudless integration.

Exposes the Tuya LAN protocol version as the firmware version.

Tuya LAN protocol does not provide a mechanism for OTA firmware updates
or for reading actual device firmware version — only the protocol version
(3.1–3.5) is negotiated during the TCP handshake.

Therefore this entity reports ``installed_version == latest_version``
(no update available) and has no install feature.
"""

from __future__ import annotations

import logging

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tuya Cloudless update entity.

    One update entity is always created, regardless of device profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    async_add_entities([TuyaCloudlessUpdateEntity(runtime.coordinator)])


class TuyaCloudlessUpdateEntity(TuyaCloudlessEntity, UpdateEntity):
    """Update entity for a Tuya Cloudless device.

    Displays the negotiated LAN protocol version (e.g. "3.3") as both the
    installed and latest firmware version.  OTA is not supported over the
    Tuya local LAN protocol, so no install feature is advertised.
    """

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = 0  # No OTA support via Tuya LAN protocol

    def __init__(self, coordinator: TuyaCloudlessCoordinator) -> None:
        """Initialise the update entity.

        Args:
            coordinator: Device coordinator carrying protocol version and state.
        """
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.gw_id}_update_firmware"
        self._attr_translation_key = "firmware_version"

    @property
    def installed_version(self) -> str | None:
        """Return the protocol version as the installed firmware version."""
        return self.coordinator.version

    @property
    def latest_version(self) -> str | None:
        """Return installed version — no update mechanism available."""
        return self.installed_version
