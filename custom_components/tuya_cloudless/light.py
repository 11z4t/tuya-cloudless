"""Support for Tuya Cloudless lights."""

import logging
from typing import Any

from homeassistant.components.light import LightEntity
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
    """Set up Tuya Cloudless lights from a config entry."""
    _LOGGER.debug("Setting up Tuya Cloudless lights")
    # TODO: Implement light discovery and setup when protocol parser is ready
    async_add_entities([])


class TuyaCloudlessLight(LightEntity):
    """Representation of a Tuya Cloudless light."""

    def __init__(self, device_id: str, name: str) -> None:
        """Initialize the light."""
        self._device_id = device_id
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{device_id}"
        self._attr_is_on = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        # TODO: Implement when protocol parser is ready
        _LOGGER.debug("Turning on light %s", self._device_id)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        # TODO: Implement when protocol parser is ready
        _LOGGER.debug("Turning off light %s", self._device_id)
        self._attr_is_on = False
        self.async_write_ha_state()
