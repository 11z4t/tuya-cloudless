"""Sensor platform for Tuya Cloudless — diagnostic sensors."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless diagnostic sensor entities."""
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    async_add_entities([
        TuyaLastSeenSensor(runtime.coordinator),
        TuyaReconnectSensor(runtime.coordinator),
    ])


class TuyaLastSeenSensor(TuyaCloudlessEntity, SensorEntity):
    """Sensor showing when the device last sent a DPS update."""

    _attr_translation_key = "last_seen"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: TuyaCloudlessCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator._gw_id}_last_seen"

    @property
    def native_value(self) -> str | None:
        """Return the ISO-8601 timestamp of the last DPS update, or None."""
        ts = self.coordinator.state.last_seen
        if ts is None:
            return None
        return ts.isoformat()


class TuyaReconnectSensor(TuyaCloudlessEntity, SensorEntity):
    """Sensor counting TCP reconnection attempts since startup."""

    _attr_translation_key = "rssi"
    _attr_icon = "mdi:wifi-sync"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: TuyaCloudlessCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator._gw_id}_reconnects"
        self._attr_name = "Reconnects"

    @property
    def native_value(self) -> int:
        """Return the total number of TCP reconnection attempts since startup."""
        return self.coordinator.state.reconnect_count
