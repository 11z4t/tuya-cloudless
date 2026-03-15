"""Switch platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

# Default DP for on/off (standard for most Tuya switches and plugs)
_DP_POWER = "1"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless switch entities."""
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    async_add_entities([TuyaCloudlessSwitch(runtime.coordinator)])


class TuyaCloudlessSwitch(TuyaCloudlessEntity, SwitchEntity):
    """Tuya Cloudless on/off switch entity.

    Maps DP "1" (boolean) to the HA switch state.
    """

    _attr_translation_key = "main_switch"

    def __init__(self, coordinator: TuyaCloudlessCoordinator) -> None:
        super().__init__(coordinator, dp_id=_DP_POWER)
        self._attr_unique_id = f"{coordinator._gw_id}_switch"

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        value = self.get_dp()
        if value is None:
            return None
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await self.async_send_dp(_DP_POWER, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await self.async_send_dp(_DP_POWER, False)
