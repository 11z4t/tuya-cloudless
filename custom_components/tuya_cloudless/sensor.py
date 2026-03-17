"""Sensor platform for Tuya Cloudless.

Creates two kinds of sensors:
  1. Profile-based sensors — from device profile ``EntitySpec`` entries
     (e.g. power consumption, current).
  2. Diagnostic sensors — always created regardless of profile
     (last seen timestamp, reconnect counter).
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from tuya_cloudless.profiles import EntitySpec

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

# Protect single-threaded Tuya devices from concurrent HA service calls
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless sensor entities.

    Creates both profile-based sensors (if any in the profile) and the
    two fixed diagnostic sensors (last seen, reconnect count).

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    entities: list[SensorEntity] = []

    # Profile-based sensors
    for spec in runtime.entity_specs:
        if spec.platform == "sensor":
            entities.append(TuyaCloudlessSensor(runtime.coordinator, spec))

    # Diagnostic sensors (always present)
    entities.append(TuyaLastSeenSensor(runtime.coordinator))
    entities.append(TuyaReconnectSensor(runtime.coordinator))

    async_add_entities(entities)


# ── Profile-based sensor ───────────────────────────────────────────────────────


class TuyaCloudlessSensor(TuyaCloudlessEntity, SensorEntity):
    """Tuya Cloudless measurement sensor (e.g. power, current, temperature).

    Value, unit, device class, and state class are all driven by the profile.
    The raw DP value is multiplied by ``spec.dp_value.scale`` before display.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the sensor.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_value.id if spec.dp_value else None
        super().__init__(coordinator, dp_id=dp_id, spec=spec)

        if spec.unit:
            self._attr_native_unit_of_measurement = spec.unit

        if spec.device_class:
            try:
                self._attr_device_class = SensorDeviceClass(spec.device_class)
            except ValueError:
                _LOGGER.warning(
                    "[%s] Unknown device_class '%s' for sensor '%s' — ignored",
                    coordinator.gw_id,
                    spec.device_class,
                    spec.name,
                )

        if spec.state_class:
            try:
                self._attr_state_class = SensorStateClass(spec.state_class)
            except ValueError:
                _LOGGER.warning(
                    "[%s] Unknown state_class '%s' for sensor '%s' — ignored",
                    coordinator.gw_id,
                    spec.state_class,
                    spec.name,
                )

    @property
    def native_value(self) -> float | str | None:
        """Return the sensor value, scaled by the DP spec's scale factor."""
        if self._spec.dp_value is None:
            return None
        raw = self.get_dp(self._spec.dp_value.id)
        if raw is None:
            return None
        scale = self._spec.dp_value.scale
        if isinstance(raw, str):
            return raw
        if scale == 1.0:
            return float(raw)
        return round(float(raw) * scale, 3)


# ── Diagnostic sensors ────────────────────────────────────────────────────────


class TuyaLastSeenSensor(TuyaCloudlessEntity, SensorEntity):
    """Sensor showing when the device last sent a status update."""

    _attr_translation_key = "last_seen"
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TuyaCloudlessCoordinator) -> None:
        """Initialise the last-seen sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.gw_id}_last_seen"

    @property
    def native_value(self) -> str | None:
        """Return the ISO-8601 timestamp of the last status update, or None."""
        ts = self.coordinator.state.last_seen
        if ts is None:
            return None
        return ts.isoformat()


class TuyaReconnectSensor(TuyaCloudlessEntity, SensorEntity):
    """Sensor counting TCP reconnection attempts since startup."""

    _attr_translation_key = "reconnects"
    _attr_icon = "mdi:wifi-sync"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TuyaCloudlessCoordinator) -> None:
        """Initialise the reconnect-count sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.gw_id}_reconnects"

    @property
    def native_value(self) -> int:
        """Return the total number of TCP reconnection attempts since startup."""
        return self.coordinator.state.reconnect_count
