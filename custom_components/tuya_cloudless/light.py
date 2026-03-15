"""Light platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from tuya_cloudless.profiles import EntitySpec

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

_HA_BRIGHTNESS_MAX = 255
_MIN_COLOR_TEMP_KELVIN = 2700
_MAX_COLOR_TEMP_KELVIN = 6500


def _tuya_to_ha_brightness(raw: int, min_raw: int, max_raw: int) -> int:
    """Map a Tuya raw brightness value (min_raw-max_raw) to 0-255."""
    span = max_raw - min_raw
    if span == 0:
        return _HA_BRIGHTNESS_MAX
    return round((raw - min_raw) / span * _HA_BRIGHTNESS_MAX)


def _ha_to_tuya_brightness(ha_value: int, min_raw: int, max_raw: int) -> int:
    """Map a HA brightness value (0-255) to Tuya raw (min_raw-max_raw)."""
    span = max_raw - min_raw
    return round(ha_value / _HA_BRIGHTNESS_MAX * span + min_raw)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless light entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "light"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessLight(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessLight(TuyaCloudlessEntity, LightEntity):
    """Tuya Cloudless dimmable white light entity.

    Supports on/off, brightness, and colour temperature. The DP identifiers
    and brightness range are read from the device profile spec, making the
    entity generic across different Tuya light models.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the light entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_power.id if spec.dp_power else "1"
        super().__init__(coordinator, dp_id=dp_id)
        self._spec = spec
        self._attr_unique_id = f"{coordinator._gw_id}_{spec.name}"
        self._attr_translation_key = spec.name
        self._attr_min_color_temp_kelvin = _MIN_COLOR_TEMP_KELVIN
        self._attr_max_color_temp_kelvin = _MAX_COLOR_TEMP_KELVIN

        # Determine supported colour modes based on spec
        has_brightness = spec.dp_brightness is not None
        has_color_temp = spec.dp_color_temp is not None

        if has_color_temp:
            self._attr_supported_color_modes = frozenset({ColorMode.COLOR_TEMP})
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif has_brightness:
            self._attr_supported_color_modes = frozenset({ColorMode.BRIGHTNESS})
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = frozenset({ColorMode.ONOFF})
            self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on, False if off, None if unknown."""
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        value = self.get_dp(dp_id)
        if value is None:
            return None
        return bool(value)

    @property
    def brightness(self) -> int | None:
        """Return the current brightness scaled to 0-255, or None if unavailable."""
        bri_spec = self._spec.dp_brightness
        if bri_spec is None:
            return None
        raw = self.get_dp(bri_spec.id)
        if raw is None:
            return None
        min_raw = bri_spec.min_raw if bri_spec.min_raw is not None else 10
        max_raw = bri_spec.max_raw if bri_spec.max_raw is not None else 1000
        return _tuya_to_ha_brightness(int(raw), min_raw, max_raw)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the current colour temperature in Kelvin, or None if unavailable."""
        ct_spec = self._spec.dp_color_temp
        if ct_spec is None:
            return None
        raw = self.get_dp(ct_spec.id)
        if raw is None:
            return None
        min_raw = ct_spec.min_raw if ct_spec.min_raw is not None else 0
        max_raw = ct_spec.max_raw if ct_spec.max_raw is not None else 1000
        span = max_raw - min_raw
        if span == 0:
            return _MIN_COLOR_TEMP_KELVIN
        ratio = (int(raw) - min_raw) / span
        return round(
            _MIN_COLOR_TEMP_KELVIN + ratio * (_MAX_COLOR_TEMP_KELVIN - _MIN_COLOR_TEMP_KELVIN)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light, optionally setting brightness and colour temperature.

        Args:
            **kwargs: HA service call attributes — ``ATTR_BRIGHTNESS`` (0-255)
                and/or ``ATTR_COLOR_TEMP_KELVIN`` (2700-6500).
        """
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        dps: dict[str, Any] = {dp_id: True}

        if ATTR_BRIGHTNESS in kwargs and self._spec.dp_brightness is not None:
            bri_spec = self._spec.dp_brightness
            ha_bri: int = kwargs[ATTR_BRIGHTNESS]
            min_raw = bri_spec.min_raw if bri_spec.min_raw is not None else 10
            max_raw = bri_spec.max_raw if bri_spec.max_raw is not None else 1000
            dps[bri_spec.id] = _ha_to_tuya_brightness(ha_bri, min_raw, max_raw)

        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._spec.dp_color_temp is not None:
            ct_spec = self._spec.dp_color_temp
            kelvin: int = kwargs[ATTR_COLOR_TEMP_KELVIN]
            min_raw = ct_spec.min_raw if ct_spec.min_raw is not None else 0
            max_raw = ct_spec.max_raw if ct_spec.max_raw is not None else 1000
            span = max_raw - min_raw
            ratio = (kelvin - _MIN_COLOR_TEMP_KELVIN) / (
                _MAX_COLOR_TEMP_KELVIN - _MIN_COLOR_TEMP_KELVIN
            )
            dps[ct_spec.id] = round(ratio * span + min_raw)

        await self.coordinator.async_send_dps(dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        await self.async_send_dp(dp_id, False)
