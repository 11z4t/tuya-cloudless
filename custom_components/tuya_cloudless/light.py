"""Light platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from tuya_cloudless.profiles import EntitySpec

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

# Protect single-threaded Tuya devices from concurrent HA service calls
PARALLEL_UPDATES = 1

_HA_BRIGHTNESS_MAX = 255
_MIN_COLOR_TEMP_KELVIN = 2700
_MAX_COLOR_TEMP_KELVIN = 6500

# Tuya color mode enum values
_TUYA_COLOR_MODE_WHITE = "white"
_TUYA_COLOR_MODE_COLOUR = "colour"

# Tuya hue raw range
_TUYA_HUE_MAX = 360

# Default Tuya saturation raw max when max_raw not specified
_TUYA_SAT_DEFAULT_MAX = 1000


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


def _tuya_to_ha_saturation(raw: int, max_raw: int) -> float:
    """Map Tuya saturation 0-max_raw to HA 0-100."""
    return round(raw / max_raw * 100, 1)


def _ha_to_tuya_saturation(ha_val: float, max_raw: int) -> int:
    """Map HA saturation 0-100 to Tuya 0-max_raw."""
    return round(ha_val / 100 * max_raw)


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
    """Tuya Cloudless light entity with support for color modes and effects.

    Supports on/off, brightness, colour temperature, HS color, and effects.
    The DP identifiers and value ranges are read from the device profile spec,
    making the entity generic across different Tuya light models.
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
        self._attr_unique_id = f"{coordinator._gw_id}_{spec.platform}_{spec.name}"
        self._attr_translation_key = spec.name
        self._attr_min_color_temp_kelvin = _MIN_COLOR_TEMP_KELVIN
        self._attr_max_color_temp_kelvin = _MAX_COLOR_TEMP_KELVIN

        # Determine supported colour modes based on spec
        has_hs = spec.dp_hs_hue is not None and spec.dp_hs_saturation is not None
        has_color_temp = spec.dp_color_temp is not None
        has_brightness = spec.dp_brightness is not None

        supported: set[ColorMode] = set()
        if has_hs:
            supported.add(ColorMode.HS)
        if has_color_temp:
            supported.add(ColorMode.COLOR_TEMP)
        if not supported and has_brightness:
            supported.add(ColorMode.BRIGHTNESS)
        if not supported:
            supported.add(ColorMode.ONOFF)

        self._attr_supported_color_modes = frozenset(supported)

        # Set up effect list if any effects are configured
        if spec.effects:
            self._attr_effect_list = list(spec.effects)
            self._attr_supported_features = LightEntityFeature.EFFECT
        else:
            self._attr_effect_list = None
            self._attr_supported_features = LightEntityFeature(0)

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on, False if off, None if unknown."""
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        value = self.get_dp(dp_id)
        if value is None:
            return None
        return bool(value)

    @property
    def color_mode(self) -> ColorMode | None:
        """Return the current active color mode.

        Reads the color mode DP if present; otherwise returns the single
        supported mode (or HS when both HS and COLOR_TEMP are supported and
        no mode DP is available).
        """
        cm_spec = self._spec.dp_color_mode
        if cm_spec is not None:
            raw_mode = self.get_dp(cm_spec.id)
            if raw_mode == _TUYA_COLOR_MODE_WHITE:
                return ColorMode.COLOR_TEMP
            if raw_mode == _TUYA_COLOR_MODE_COLOUR:
                return ColorMode.HS
            # Unknown / unset — fall through to default logic below

        # No color mode DP — return the single supported mode
        modes = self._attr_supported_color_modes
        if len(modes) == 1:
            return next(iter(modes))

        # Multiple modes but no mode DP — prefer HS if available
        if ColorMode.HS in modes:
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP

        return ColorMode.ONOFF

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
    def hs_color(self) -> tuple[float, float] | None:
        """Return the current hue and saturation as (0-360, 0-100), or None."""
        hue_spec = self._spec.dp_hs_hue
        sat_spec = self._spec.dp_hs_saturation
        if hue_spec is None or sat_spec is None:
            return None
        raw_hue = self.get_dp(hue_spec.id)
        raw_sat = self.get_dp(sat_spec.id)
        if raw_hue is None or raw_sat is None:
            return None
        hue_max = hue_spec.max_raw if hue_spec.max_raw is not None else _TUYA_HUE_MAX
        sat_max = sat_spec.max_raw if sat_spec.max_raw is not None else _TUYA_SAT_DEFAULT_MAX
        hue = round(int(raw_hue) / hue_max * _TUYA_HUE_MAX, 1)
        sat = _tuya_to_ha_saturation(int(raw_sat), sat_max)
        return (hue, sat)

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

    @property
    def effect(self) -> str | None:
        """Return the current active effect, or None if no effect is active."""
        # Effects are not currently read from a DP — this requires an effect DP
        # to be added to the profile spec in a future iteration.
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light, optionally setting brightness, color, or effect.

        Args:
            **kwargs: HA service call attributes — ``ATTR_BRIGHTNESS`` (0-255),
                ``ATTR_COLOR_TEMP_KELVIN`` (2700-6500), ``ATTR_HS_COLOR``
                ((hue 0-360, sat 0-100)), or ``ATTR_EFFECT``.
        """
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        dps: dict[str, Any] = {dp_id: True}

        if (
            ATTR_HS_COLOR in kwargs
            and self._spec.dp_hs_hue is not None
            and self._spec.dp_hs_saturation is not None
        ):
            hue_spec = self._spec.dp_hs_hue
            sat_spec = self._spec.dp_hs_saturation
            ha_hue: float
            ha_sat: float
            ha_hue, ha_sat = kwargs[ATTR_HS_COLOR]
            hue_max = hue_spec.max_raw if hue_spec.max_raw is not None else _TUYA_HUE_MAX
            sat_max = sat_spec.max_raw if sat_spec.max_raw is not None else _TUYA_SAT_DEFAULT_MAX
            dps[hue_spec.id] = round(ha_hue / _TUYA_HUE_MAX * hue_max)
            dps[sat_spec.id] = _ha_to_tuya_saturation(ha_sat, sat_max)
            # Switch color mode DP to "colour" if available
            if self._spec.dp_color_mode is not None:
                dps[self._spec.dp_color_mode.id] = _TUYA_COLOR_MODE_COLOUR

        elif ATTR_COLOR_TEMP_KELVIN in kwargs and self._spec.dp_color_temp is not None:
            ct_spec = self._spec.dp_color_temp
            kelvin: int = kwargs[ATTR_COLOR_TEMP_KELVIN]
            min_raw = ct_spec.min_raw if ct_spec.min_raw is not None else 0
            max_raw = ct_spec.max_raw if ct_spec.max_raw is not None else 1000
            span = max_raw - min_raw
            ratio = (kelvin - _MIN_COLOR_TEMP_KELVIN) / (
                _MAX_COLOR_TEMP_KELVIN - _MIN_COLOR_TEMP_KELVIN
            )
            dps[ct_spec.id] = round(ratio * span + min_raw)
            # Switch color mode DP to "white" if available
            if self._spec.dp_color_mode is not None:
                dps[self._spec.dp_color_mode.id] = _TUYA_COLOR_MODE_WHITE

        if ATTR_BRIGHTNESS in kwargs and self._spec.dp_brightness is not None:
            bri_spec = self._spec.dp_brightness
            ha_bri: int = kwargs[ATTR_BRIGHTNESS]
            min_raw = bri_spec.min_raw if bri_spec.min_raw is not None else 10
            max_raw = bri_spec.max_raw if bri_spec.max_raw is not None else 1000
            dps[bri_spec.id] = _ha_to_tuya_brightness(ha_bri, min_raw, max_raw)

        if ATTR_EFFECT in kwargs and self._attr_effect_list:
            effect: str = kwargs[ATTR_EFFECT]
            _LOGGER.debug("Effect requested: %s (not yet mapped to DP)", effect)

        await self.coordinator.async_send_dps(dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        await self.async_send_dp(dp_id, False)
