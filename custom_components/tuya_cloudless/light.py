"""Light platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

# Standard Tuya light DPs
_DP_POWER = "1"
_DP_MODE = "2"       # "white" / "colour" / "scene"
_DP_BRIGHTNESS = "3" # 10–1000
_DP_COLOR_TEMP = "4" # 0 (warm) – 1000 (cool)

_TUYA_BRIGHTNESS_MAX = 1000
_TUYA_BRIGHTNESS_MIN = 10
_HA_BRIGHTNESS_MAX = 255


def _tuya_to_ha_brightness(value: int) -> int:
    return round(
        (value - _TUYA_BRIGHTNESS_MIN)
        / (_TUYA_BRIGHTNESS_MAX - _TUYA_BRIGHTNESS_MIN)
        * _HA_BRIGHTNESS_MAX
    )


def _ha_to_tuya_brightness(value: int) -> int:
    return round(
        value / _HA_BRIGHTNESS_MAX * (_TUYA_BRIGHTNESS_MAX - _TUYA_BRIGHTNESS_MIN)
        + _TUYA_BRIGHTNESS_MIN
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless light entities."""
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    async_add_entities([TuyaCloudlessLight(runtime.coordinator)])


class TuyaCloudlessLight(TuyaCloudlessEntity, LightEntity):
    """Tuya Cloudless dimmable white light entity.

    Supports on/off, brightness (DP 3), and colour temperature (DP 4).
    """

    _attr_translation_key = "main_light"
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_min_color_temp_kelvin = 2700
    _attr_max_color_temp_kelvin = 6500

    def __init__(self, coordinator: TuyaCloudlessCoordinator) -> None:
        super().__init__(coordinator, dp_id=_DP_POWER)
        self._attr_unique_id = f"{coordinator._gw_id}_light"  # noqa: SLF001

    @property
    def is_on(self) -> bool | None:
        value = self.get_dp(_DP_POWER)
        if value is None:
            return None
        return bool(value)

    @property
    def brightness(self) -> int | None:
        raw = self.get_dp(_DP_BRIGHTNESS)
        if raw is None:
            return None
        return _tuya_to_ha_brightness(int(raw))

    @property
    def color_temp_kelvin(self) -> int | None:
        raw = self.get_dp(_DP_COLOR_TEMP)
        if raw is None:
            return None
        # Tuya 0=warm(2700K), 1000=cool(6500K)
        ratio = int(raw) / _TUYA_BRIGHTNESS_MAX
        return round(
            self._attr_min_color_temp_kelvin
            + ratio * (self._attr_max_color_temp_kelvin - self._attr_min_color_temp_kelvin)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        dps: dict[str, Any] = {_DP_POWER: True}

        if ATTR_BRIGHTNESS in kwargs:
            ha_bri: int = kwargs[ATTR_BRIGHTNESS]
            dps[_DP_BRIGHTNESS] = _ha_to_tuya_brightness(ha_bri)

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin: int = kwargs[ATTR_COLOR_TEMP_KELVIN]
            ratio = (kelvin - self._attr_min_color_temp_kelvin) / (
                self._attr_max_color_temp_kelvin - self._attr_min_color_temp_kelvin
            )
            dps[_DP_COLOR_TEMP] = round(ratio * _TUYA_BRIGHTNESS_MAX)

        await self.coordinator.async_send_dps(dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_send_dp(_DP_POWER, False)
