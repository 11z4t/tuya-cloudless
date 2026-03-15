"""Binary sensor platform for Tuya Cloudless.

Creates binary sensor entities from device profile specifications.
Typical use cases: overload detection, leak detection, door/window sensors.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
    """Set up Tuya Cloudless binary sensor entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "binary_sensor"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessBinarySensor(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessBinarySensor(TuyaCloudlessEntity, BinarySensorEntity):
    """Tuya Cloudless binary sensor entity.

    Maps a boolean DP to a HA binary sensor (on/off, problem/ok, etc.).
    Device class and name are driven by the device profile.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the binary sensor.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_power.id if spec.dp_power else None
        super().__init__(coordinator, dp_id=dp_id)
        self._spec = spec
        self._attr_unique_id = f"{coordinator._gw_id}_{spec.platform}_{spec.name}"
        self._attr_translation_key = spec.name

        if spec.device_class:
            from homeassistant.components.binary_sensor import BinarySensorDeviceClass

            with __import__("contextlib").suppress(ValueError):
                self._attr_device_class = BinarySensorDeviceClass(spec.device_class)

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is active (problem/on/open)."""
        value = self.get_dp()
        if value is None:
            return None
        return bool(value)
