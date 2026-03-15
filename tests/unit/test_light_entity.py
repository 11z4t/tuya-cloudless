"""Unit tests for the Tuya Cloudless light entity.

Tests cover color mode detection, brightness/color-temp conversion,
HS color mapping, and turn-on/turn-off logic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure lib/ is importable
_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from tuya_cloudless.profiles import DPSpec, EntitySpec  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(dps: dict[str, Any] | None = None, gw_id: str = "gw001") -> MagicMock:
    coord = MagicMock()
    coord._gw_id = gw_id
    coord.gw_id = gw_id
    coord.device_name = gw_id
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = MagicMock()
    coord.state.available = True
    coord.state.dps = dps or {}
    coord.async_send_dps = AsyncMock()
    return coord


def _make_light_spec(
    dp_power_id: str = "1",
    dp_brightness_id: str | None = "3",
    dp_color_temp_id: str | None = "4",
    dp_hs_hue_id: str | None = None,
    dp_hs_sat_id: str | None = None,
    dp_color_mode_id: str | None = None,
    effects: tuple[str, ...] = (),
) -> EntitySpec:
    return EntitySpec(
        platform="light",
        name="main_light",
        dp_power=DPSpec(id=dp_power_id, type="bool"),
        dp_brightness=(
            DPSpec(id=dp_brightness_id, type="int", min_raw=10, max_raw=1000)
            if dp_brightness_id
            else None
        ),
        dp_color_temp=(
            DPSpec(id=dp_color_temp_id, type="int", min_raw=0, max_raw=1000)
            if dp_color_temp_id
            else None
        ),
        dp_hs_hue=DPSpec(id=dp_hs_hue_id, type="int", max_raw=360) if dp_hs_hue_id else None,
        dp_hs_saturation=(
            DPSpec(id=dp_hs_sat_id, type="int", max_raw=1000) if dp_hs_sat_id else None
        ),
        dp_color_mode=DPSpec(id=dp_color_mode_id, type="enum") if dp_color_mode_id else None,
        effects=effects,
    )


def _make_light(dps: dict[str, Any] | None = None, spec: EntitySpec | None = None) -> Any:
    from custom_components.tuya_cloudless.light import TuyaCloudlessLight

    coord = _make_coordinator(dps)
    _spec = spec or _make_light_spec()
    entity = TuyaCloudlessLight.__new__(TuyaCloudlessLight)
    entity.coordinator = coord
    entity._spec = _spec
    entity._dp_id = _spec.dp_power.id if _spec.dp_power else "1"
    entity._attr_unique_id = f"{coord._gw_id}_{_spec.platform}_{_spec.name}"
    entity._attr_translation_key = _spec.name
    entity._attr_min_color_temp_kelvin = 2700
    entity._attr_max_color_temp_kelvin = 6500

    # Re-run supported color mode logic from __init__
    from homeassistant.components.light import ColorMode, LightEntityFeature

    has_hs = _spec.dp_hs_hue is not None and _spec.dp_hs_saturation is not None
    has_color_temp = _spec.dp_color_temp is not None
    has_brightness = _spec.dp_brightness is not None

    supported: set[ColorMode] = set()
    if has_hs:
        supported.add(ColorMode.HS)
    if has_color_temp:
        supported.add(ColorMode.COLOR_TEMP)
    if not supported and has_brightness:
        supported.add(ColorMode.BRIGHTNESS)
    if not supported:
        supported.add(ColorMode.ONOFF)
    entity._attr_supported_color_modes = frozenset(supported)

    if _spec.effects:
        entity._attr_effect_list = list(_spec.effects)
        entity._attr_supported_features = LightEntityFeature.EFFECT
    else:
        entity._attr_effect_list = None
        entity._attr_supported_features = LightEntityFeature(0)

    return entity


# ── is_on ──────────────────────────────────────────────────────────────────────


class TestLightIsOn:
    def test_is_on_true(self) -> None:
        e = _make_light({"1": True})
        assert e.get_dp("1") is True

    def test_is_on_false(self) -> None:
        e = _make_light({"1": False})
        assert e.get_dp("1") is False

    def test_is_on_none_when_missing(self) -> None:
        e = _make_light({})
        assert e.get_dp("1") is None

    def test_unique_id_includes_platform(self) -> None:
        e = _make_light()
        assert e._attr_unique_id == "gw001_light_main_light"


# ── color_mode ─────────────────────────────────────────────────────────────────


class TestLightColorMode:
    def test_brightness_only_mode(self) -> None:
        from homeassistant.components.light import ColorMode

        spec = _make_light_spec(dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert ColorMode.BRIGHTNESS in e._attr_supported_color_modes
        assert e.color_mode == ColorMode.BRIGHTNESS

    def test_color_temp_mode(self) -> None:
        from homeassistant.components.light import ColorMode

        e = _make_light()
        assert ColorMode.COLOR_TEMP in e._attr_supported_color_modes
        assert e.color_mode == ColorMode.COLOR_TEMP

    def test_onoff_only_mode(self) -> None:
        from homeassistant.components.light import ColorMode

        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert ColorMode.ONOFF in e._attr_supported_color_modes
        assert e.color_mode == ColorMode.ONOFF

    def test_hs_mode_when_hs_configured(self) -> None:
        from homeassistant.components.light import ColorMode

        spec = _make_light_spec(dp_hs_hue_id="5", dp_hs_sat_id="6")
        e = _make_light(spec=spec)
        assert ColorMode.HS in e._attr_supported_color_modes
        assert e.color_mode == ColorMode.HS

    def test_color_mode_dp_white(self) -> None:
        from homeassistant.components.light import ColorMode

        spec = _make_light_spec(
            dp_hs_hue_id="5",
            dp_hs_sat_id="6",
            dp_color_mode_id="7",
        )
        e = _make_light({"7": "white"}, spec=spec)
        assert e.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_dp_colour(self) -> None:
        from homeassistant.components.light import ColorMode

        spec = _make_light_spec(
            dp_hs_hue_id="5",
            dp_hs_sat_id="6",
            dp_color_mode_id="7",
        )
        e = _make_light({"7": "colour"}, spec=spec)
        assert e.color_mode == ColorMode.HS


# ── brightness ─────────────────────────────────────────────────────────────────


class TestLightBrightness:
    def test_brightness_scaled_from_tuya(self) -> None:
        # 1000 raw (max) → 255 HA
        e = _make_light({"3": 1000})
        assert e.brightness == 255

    def test_brightness_min(self) -> None:
        # 10 raw (min) → ~3 HA
        e = _make_light({"3": 10})
        assert isinstance(e.brightness, int)
        assert e.brightness >= 0

    def test_brightness_none_when_missing(self) -> None:
        e = _make_light({})
        assert e.brightness is None

    def test_brightness_none_when_no_spec(self) -> None:
        spec = _make_light_spec(dp_brightness_id=None)
        e = _make_light(spec=spec)
        assert e.brightness is None


# ── color_temp ─────────────────────────────────────────────────────────────────


class TestLightColorTemp:
    def test_color_temp_cold_end(self) -> None:
        # raw=1000 (max) → 6500K (cool)
        e = _make_light({"4": 1000})
        assert e.color_temp_kelvin == 6500

    def test_color_temp_warm_end(self) -> None:
        # raw=0 (min) → 2700K (warm)
        e = _make_light({"4": 0})
        assert e.color_temp_kelvin == 2700

    def test_color_temp_midpoint(self) -> None:
        # raw=500 → ~4600K
        e = _make_light({"4": 500})
        k = e.color_temp_kelvin
        assert k is not None
        assert 2700 < k < 6500

    def test_color_temp_none_when_missing(self) -> None:
        e = _make_light({})
        assert e.color_temp_kelvin is None

    def test_color_temp_none_when_no_spec(self) -> None:
        spec = _make_light_spec(dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert e.color_temp_kelvin is None


# ── hs_color ───────────────────────────────────────────────────────────────────


class TestLightHSColor:
    def test_hs_color_returns_tuple(self) -> None:
        spec = _make_light_spec(dp_hs_hue_id="5", dp_hs_sat_id="6")
        e = _make_light({"5": 180, "6": 500}, spec=spec)
        hs = e.hs_color
        assert hs is not None
        hue, sat = hs
        assert 0 <= hue <= 360
        assert 0 <= sat <= 100

    def test_hs_color_none_when_no_spec(self) -> None:
        e = _make_light({"5": 180, "6": 500})
        assert e.hs_color is None

    def test_hs_color_none_when_missing_dp(self) -> None:
        spec = _make_light_spec(dp_hs_hue_id="5", dp_hs_sat_id="6")
        e = _make_light({}, spec=spec)
        assert e.hs_color is None


# ── effect ─────────────────────────────────────────────────────────────────────


class TestLightEffect:
    def test_effect_list_set_from_spec(self) -> None:
        spec = _make_light_spec(effects=("rainbow", "pulse"))
        e = _make_light(spec=spec)
        assert e._attr_effect_list == ["rainbow", "pulse"]

    def test_effect_list_none_when_no_effects(self) -> None:
        e = _make_light()
        assert e._attr_effect_list is None

    def test_effect_property_returns_none(self) -> None:
        # Effects not yet read from DP
        e = _make_light()
        assert e.effect is None


# ── async_turn_on ──────────────────────────────────────────────────────────────


class TestLightTurnOn:
    @pytest.mark.asyncio
    async def test_turn_on_sends_true(self) -> None:
        e = _make_light()
        await e.async_turn_on()
        e.coordinator.async_send_dps.assert_called_once()
        call_dps = e.coordinator.async_send_dps.call_args[0][0]
        assert call_dps.get("1") is True

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness(self) -> None:
        from homeassistant.components.light import ATTR_BRIGHTNESS

        e = _make_light()
        await e.async_turn_on(**{ATTR_BRIGHTNESS: 128})
        call_dps = e.coordinator.async_send_dps.call_args[0][0]
        assert "3" in call_dps
        assert isinstance(call_dps["3"], int)

    @pytest.mark.asyncio
    async def test_turn_on_with_color_temp(self) -> None:
        from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN

        e = _make_light()
        await e.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
        call_dps = e.coordinator.async_send_dps.call_args[0][0]
        assert "4" in call_dps

    @pytest.mark.asyncio
    async def test_turn_on_with_hs_color(self) -> None:
        from homeassistant.components.light import ATTR_HS_COLOR

        spec = _make_light_spec(dp_hs_hue_id="5", dp_hs_sat_id="6")
        e = _make_light(spec=spec)
        await e.async_turn_on(**{ATTR_HS_COLOR: (180.0, 80.0)})
        call_dps = e.coordinator.async_send_dps.call_args[0][0]
        assert "5" in call_dps
        assert "6" in call_dps

    @pytest.mark.asyncio
    async def test_turn_on_with_hs_and_color_mode_dp(self) -> None:
        from homeassistant.components.light import ATTR_HS_COLOR

        spec = _make_light_spec(dp_hs_hue_id="5", dp_hs_sat_id="6", dp_color_mode_id="7")
        e = _make_light(spec=spec)
        await e.async_turn_on(**{ATTR_HS_COLOR: (180.0, 80.0)})
        call_dps = e.coordinator.async_send_dps.call_args[0][0]
        assert call_dps.get("7") == "colour"

    @pytest.mark.asyncio
    async def test_turn_on_with_color_temp_and_color_mode_dp(self) -> None:
        from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN

        spec = _make_light_spec(
            dp_hs_hue_id="5",
            dp_hs_sat_id="6",
            dp_color_temp_id="4",
            dp_color_mode_id="7",
        )
        e = _make_light(spec=spec)
        await e.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
        call_dps = e.coordinator.async_send_dps.call_args[0][0]
        assert call_dps.get("7") == "white"


# ── async_turn_off ─────────────────────────────────────────────────────────────


class TestLightTurnOff:
    @pytest.mark.asyncio
    async def test_turn_off_sends_false(self) -> None:
        e = _make_light({"1": True})
        await e.async_turn_off()
        e.coordinator.async_send_dps.assert_called_once()
        call_dps = e.coordinator.async_send_dps.call_args[0][0]
        assert call_dps.get("1") is False


# ── Constructor (covers __init__ lines) ────────────────────────────────────────


class TestLightConstructor:
    """Tests that actually call TuyaCloudlessLight.__init__ to cover lines 111-142."""

    def test_constructor_onoff_mode(self) -> None:
        from homeassistant.components.light import ColorMode

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None)
        light = TuyaCloudlessLight(coord, spec)

        assert light._attr_unique_id == "gw001_light_main_light"
        assert ColorMode.ONOFF in light._attr_supported_color_modes

    def test_constructor_brightness_mode(self) -> None:
        from homeassistant.components.light import ColorMode

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_color_temp_id=None)
        light = TuyaCloudlessLight(coord, spec)

        assert ColorMode.BRIGHTNESS in light._attr_supported_color_modes

    def test_constructor_color_temp_mode(self) -> None:
        from homeassistant.components.light import ColorMode

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec()
        light = TuyaCloudlessLight(coord, spec)

        assert ColorMode.COLOR_TEMP in light._attr_supported_color_modes
        assert light._attr_min_color_temp_kelvin == 2700
        assert light._attr_max_color_temp_kelvin == 6500

    def test_constructor_hs_mode(self) -> None:
        from homeassistant.components.light import ColorMode

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_hs_hue_id="5", dp_hs_sat_id="6")
        light = TuyaCloudlessLight(coord, spec)

        assert ColorMode.HS in light._attr_supported_color_modes

    def test_constructor_with_effects(self) -> None:
        from homeassistant.components.light import LightEntityFeature

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(effects=("rainbow", "pulse"))
        light = TuyaCloudlessLight(coord, spec)

        assert light._attr_effect_list == ["rainbow", "pulse"]
        assert LightEntityFeature.EFFECT in light._attr_supported_features

    def test_constructor_no_effects(self) -> None:
        from homeassistant.components.light import LightEntityFeature

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec()
        light = TuyaCloudlessLight(coord, spec)

        assert light._attr_effect_list is None
        assert LightEntityFeature.EFFECT not in light._attr_supported_features

    def test_constructor_no_dp_power_defaults_to_1(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = EntitySpec(platform="light", name="nopow", dp_power=None)
        light = TuyaCloudlessLight(coord, spec)
        assert light._dp_id == "1"


# ── is_on property ─────────────────────────────────────────────────────────────


class TestLightIsOnProperty:
    def test_is_on_true(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"1": True})
        spec = _make_light_spec()
        light = TuyaCloudlessLight(coord, spec)
        assert light.is_on is True

    def test_is_on_false(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"1": False})
        spec = _make_light_spec()
        light = TuyaCloudlessLight(coord, spec)
        assert light.is_on is False

    def test_is_on_none_when_missing(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({})
        spec = _make_light_spec()
        light = TuyaCloudlessLight(coord, spec)
        assert light.is_on is None


# ── Edge cases ─────────────────────────────────────────────────────────────────


class TestLightEdgeCases:
    def test_brightness_span_zero(self) -> None:
        from custom_components.tuya_cloudless.light import _tuya_to_ha_brightness

        # span=0 means min_raw == max_raw — return max
        result = _tuya_to_ha_brightness(500, 500, 500)
        assert result == 255

    def test_color_temp_kelvin_span_zero(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"4": 500})
        # span=0: min_raw == max_raw
        spec = EntitySpec(
            platform="light",
            name="ct_zero",
            dp_power=DPSpec(id="1", type="bool"),
            dp_color_temp=DPSpec(id="4", type="int", min_raw=500, max_raw=500),
        )
        light = TuyaCloudlessLight(coord, spec)
        assert light.color_temp_kelvin == 2700  # _MIN_COLOR_TEMP_KELVIN

    def test_color_mode_fallback_to_color_temp(self) -> None:
        """When HS not in modes but COLOR_TEMP is, fallback to COLOR_TEMP."""
        from homeassistant.components.light import ColorMode

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({})
        spec = _make_light_spec(dp_hs_hue_id=None, dp_hs_sat_id=None)
        light = TuyaCloudlessLight(coord, spec)
        # COLOR_TEMP supported, no HS — should fall back
        assert light.color_mode == ColorMode.COLOR_TEMP

    @pytest.mark.asyncio
    async def test_turn_on_with_effect(self) -> None:
        from homeassistant.components.light import ATTR_EFFECT

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(effects=("rainbow",))
        light = TuyaCloudlessLight(coord, spec)
        # Should not raise — just logs
        await light.async_turn_on(**{ATTR_EFFECT: "rainbow"})
        coord.async_send_dps.assert_called_once()
