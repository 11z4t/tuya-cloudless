"""Support for Tuya Cloudless switches."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless switches from a config entry."""
    _LOGGER.debug("Setting up Tuya Cloudless switches")
    # TODO: Implement switch discovery and setup when protocol parser is ready
    async_add_entities([])


class TuyaCloudlessSwitch(SwitchEntity):
    """Representation of a Tuya Cloudless switch."""

    def __init__(self, device_id: str, name: str) -> None:
        """Initialize the switch."""
        self._device_id = device_id
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{device_id}"
        self._attr_is_on = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        # TODO: Implement when protocol parser is ready
        _LOGGER.debug("Turning on switch %s", self._device_id)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        # TODO: Implement when protocol parser is ready
        _LOGGER.debug("Turning off switch %s", self._device_id)
        self._attr_is_on = False
        self.async_write_ha_state()
