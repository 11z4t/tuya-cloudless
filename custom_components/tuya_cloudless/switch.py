"""Switch platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up Tuya Cloudless switch entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "switch"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessSwitch(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessSwitch(TuyaCloudlessEntity, SwitchEntity):
    """Tuya Cloudless on/off switch entity.

    Maps a boolean DP to the HA switch state. The DP identifier and entity
    name are driven by the device profile rather than hardcoded values.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the switch entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_power.id if spec.dp_power else "1"
        super().__init__(coordinator, dp_id=dp_id)
        self._spec = spec
        self._attr_unique_id = f"{coordinator._gw_id}_{spec.platform}_{spec.name}"
        self._attr_translation_key = spec.name

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        value = self.get_dp()
        if value is None:
            return None
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        await self.async_send_dp(dp_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        await self.async_send_dp(dp_id, False)
