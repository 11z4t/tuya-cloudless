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
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from tuya_cloudless.profiles import EntitySpec

from .coordinator import TuyaCloudlessCoordinator
from .entity import RestoreStateMixin, TuyaCloudlessEntity

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

# Tuya colour_data DP: 12-char lowercase hex "HHHHSSSSBBBB"
# H = hue 0-360, S = saturation 0-1000, B = brightness/value 0-1000
_TUYA_COLOUR_DATA_LEN = 12
_TUYA_COLOUR_DATA_SAT_MAX = 1000
_TUYA_COLOUR_DATA_VAL_MAX = 1000


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


def _encode_colour_data(hue: float, sat_pct: float, brightness_255: int) -> str:
    """Encode HA HS colour + brightness into a 12-char Tuya colour_data hex string.

    Args:
        hue:            HA hue, 0.0-360.0.
        sat_pct:        HA saturation, 0.0-100.0.
        brightness_255: HA brightness, 0-255.

    Returns:
        12-char lowercase hex string ``HHHHSSSSBBBB``.
    """
    h = round(min(360, max(0, hue)))
    s = round(min(_TUYA_COLOUR_DATA_SAT_MAX, max(0, sat_pct / 100 * _TUYA_COLOUR_DATA_SAT_MAX)))
    v = round(
        min(
            _TUYA_COLOUR_DATA_VAL_MAX,
            max(0, brightness_255 / _HA_BRIGHTNESS_MAX * _TUYA_COLOUR_DATA_VAL_MAX),
        )
    )
    return f"{h:04x}{s:04x}{v:04x}"


def _decode_colour_data(raw: str) -> tuple[float, float, int] | None:
    """Decode a 12-char Tuya colour_data hex string to ``(hue, sat_pct, brightness_255)``.

    Args:
        raw: 12-char hex string ``HHHHSSSSBBBB``.

    Returns:
        Tuple ``(hue 0-360, sat_pct 0-100, brightness 0-255)`` or ``None`` if invalid.
    """
    if not isinstance(raw, str) or len(raw) != _TUYA_COLOUR_DATA_LEN:
        return None
    try:
        h = int(raw[0:4], 16)
        s = int(raw[4:8], 16)
        v = int(raw[8:12], 16)
    except ValueError:
        return None
    hue = float(min(360, h))
    sat_pct = round(min(100.0, s / _TUYA_COLOUR_DATA_SAT_MAX * 100), 1)
    brightness_255 = round(
        min(_HA_BRIGHTNESS_MAX, v / _TUYA_COLOUR_DATA_VAL_MAX * _HA_BRIGHTNESS_MAX)
    )
    return (hue, sat_pct, brightness_255)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "light"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessLight(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessLight(RestoreStateMixin, TuyaCloudlessEntity, LightEntity):
    _attr_assumed_state: bool = False

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        dp_id = spec.dp_power.id if spec.dp_power else "1"
        super().__init__(coordinator, dp_id=dp_id, spec=spec)
        self._attr_min_color_temp_kelvin = _MIN_COLOR_TEMP_KELVIN
        self._attr_max_color_temp_kelvin = _MAX_COLOR_TEMP_KELVIN
        self._optimistic_state: bool | None = None
        has_hs = (
            spec.dp_hs_hue is not None and spec.dp_hs_saturation is not None
        ) or spec.dp_colour_data is not None
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
        if spec.effects:
            self._attr_effect_list = list(spec.effects)
            self._attr_supported_features = LightEntityFeature.EFFECT
        else:
            self._attr_effect_list = None
            self._attr_supported_features = LightEntityFeature(0)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._restored_state in (STATE_ON, STATE_OFF):
            self._optimistic_state = self._restored_state == STATE_ON

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when the coordinator delivers live device data."""
        self._optimistic_state = None
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on.

        Optimistic state (set before a command is confirmed) takes priority.
        Falls back to live DP once the coordinator delivers a device update.
        """
        if self._optimistic_state is not None:
            return self._optimistic_state
        dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
        dp_val = self.get_dp(dp_id)
        if dp_val is not None:
            return bool(dp_val)
        return None

    @property
    def color_mode(self) -> ColorMode | None:
        cm_spec = self._spec.dp_color_mode
        if cm_spec is not None:
            raw_mode = self.get_dp(cm_spec.id)
            if raw_mode == _TUYA_COLOR_MODE_WHITE:
                return ColorMode.COLOR_TEMP
            if raw_mode == _TUYA_COLOR_MODE_COLOUR:
                return ColorMode.HS
        modes = self._attr_supported_color_modes
        if len(modes) == 1:
            return next(iter(modes))
        if ColorMode.HS in modes:
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        return ColorMode.ONOFF

    @property
    def brightness(self) -> int | None:
        # When in colour mode with a colour_data DP, brightness is the V component
        cd_spec = self._spec.dp_colour_data
        if cd_spec is not None and self.color_mode == ColorMode.HS:
            raw = self.get_dp(cd_spec.id)
            if raw is not None:
                decoded = _decode_colour_data(str(raw))
                if decoded is not None:
                    return decoded[2]
            return None
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
        # Try compound colour_data DP first
        cd_spec = self._spec.dp_colour_data
        if cd_spec is not None:
            raw = self.get_dp(cd_spec.id)
            if raw is not None:
                decoded = _decode_colour_data(str(raw))
                if decoded is not None:
                    return (decoded[0], decoded[1])
            return None
        # Fall back to separate hue/sat DPs
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
        # Clamp to valid HA ranges in case the device reports an out-of-range value
        hue = max(0.0, min(360.0, round(int(raw_hue) / hue_max * _TUYA_HUE_MAX, 1)))
        sat = max(0.0, min(100.0, _tuya_to_ha_saturation(int(raw_sat), sat_max)))
        return (hue, sat)

    @property
    def color_temp_kelvin(self) -> int | None:
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
        # Clamp result in case the device reports an out-of-range raw value
        return max(
            _MIN_COLOR_TEMP_KELVIN,
            min(
                _MAX_COLOR_TEMP_KELVIN,
                round(
                    _MIN_COLOR_TEMP_KELVIN
                    + ratio * (_MAX_COLOR_TEMP_KELVIN - _MIN_COLOR_TEMP_KELVIN)
                ),
            ),
        )

    @property
    def effect(self) -> str | None:
        scene_spec = self._spec.dp_scene
        if scene_spec is None:
            return None
        raw = self.get_dp(scene_spec.id)
        if raw is None:
            return None
        return str(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._optimistic_state = True
        self.async_write_ha_state()
        try:
            dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
            dps: dict[str, Any] = {dp_id: True}
            has_hs_kwarg = ATTR_HS_COLOR in kwargs
            has_bri_kwarg = ATTR_BRIGHTNESS in kwargs

            # ── HS colour ──────────────────────────────────────────────────────
            if has_hs_kwarg:
                ha_hue: float
                ha_sat: float
                ha_hue, ha_sat = kwargs[ATTR_HS_COLOR]
                cd_spec = self._spec.dp_colour_data
                if cd_spec is not None:
                    # Fold brightness into V; use new value if provided, else current
                    bri = (
                        int(kwargs[ATTR_BRIGHTNESS])
                        if has_bri_kwarg
                        else (self.brightness or _HA_BRIGHTNESS_MAX)
                    )
                    dps[cd_spec.id] = _encode_colour_data(ha_hue, ha_sat, bri)
                    if self._spec.dp_color_mode is not None:
                        dps[self._spec.dp_color_mode.id] = _TUYA_COLOR_MODE_COLOUR
                elif self._spec.dp_hs_hue is not None and self._spec.dp_hs_saturation is not None:
                    hue_spec = self._spec.dp_hs_hue
                    sat_spec = self._spec.dp_hs_saturation
                    hue_max = hue_spec.max_raw if hue_spec.max_raw is not None else _TUYA_HUE_MAX
                    sat_max = (
                        sat_spec.max_raw if sat_spec.max_raw is not None else _TUYA_SAT_DEFAULT_MAX
                    )
                    dps[hue_spec.id] = round(ha_hue / _TUYA_HUE_MAX * hue_max)
                    dps[sat_spec.id] = _ha_to_tuya_saturation(ha_sat, sat_max)
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
                if self._spec.dp_color_mode is not None:
                    dps[self._spec.dp_color_mode.id] = _TUYA_COLOR_MODE_WHITE

            # ── Brightness ─────────────────────────────────────────────────────
            if has_bri_kwarg:
                ha_bri: int = kwargs[ATTR_BRIGHTNESS]
                cd_spec = self._spec.dp_colour_data
                if cd_spec is not None and has_hs_kwarg:
                    # Already folded into colour_data above
                    pass
                elif cd_spec is not None and self.color_mode == ColorMode.HS:
                    # Standalone brightness change in colour mode: update V component
                    current_hs = self.hs_color
                    if current_hs is not None:
                        dps[cd_spec.id] = _encode_colour_data(current_hs[0], current_hs[1], ha_bri)
                        if self._spec.dp_color_mode is not None:
                            dps[self._spec.dp_color_mode.id] = _TUYA_COLOR_MODE_COLOUR
                elif self._spec.dp_brightness is not None:
                    bri_spec = self._spec.dp_brightness
                    min_raw = bri_spec.min_raw if bri_spec.min_raw is not None else 10
                    max_raw = bri_spec.max_raw if bri_spec.max_raw is not None else 1000
                    dps[bri_spec.id] = _ha_to_tuya_brightness(ha_bri, min_raw, max_raw)

            # ── Scene / effect ─────────────────────────────────────────────────
            if ATTR_EFFECT in kwargs and self._attr_effect_list:
                effect: str = kwargs[ATTR_EFFECT]
                scene_spec = self._spec.dp_scene
                if scene_spec is not None:
                    if effect in self._attr_effect_list:
                        dps[scene_spec.id] = effect
                    else:
                        _LOGGER.warning(
                            "Effect '%s' not in effect_list for '%s'; ignoring",
                            effect,
                            self._attr_unique_id,
                        )
                else:
                    _LOGGER.debug("Effect requested: %s (no dp_scene configured)", effect)

            await self.coordinator.async_send_dps(dps)
        except HomeAssistantError:
            self._optimistic_state = None
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._optimistic_state = False
        self.async_write_ha_state()
        try:
            dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
            await self.async_send_dp(dp_id, False)
        except HomeAssistantError:
            self._optimistic_state = None
            self.async_write_ha_state()
            raise
