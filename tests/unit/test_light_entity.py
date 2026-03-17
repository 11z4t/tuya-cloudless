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
    dp_scene_id: str | None = None,
    dp_colour_data_id: str | None = None,
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
        dp_scene=DPSpec(id=dp_scene_id, type="enum") if dp_scene_id else None,
        dp_colour_data=DPSpec(id=dp_colour_data_id, type="str") if dp_colour_data_id else None,
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

    has_hs = (
        _spec.dp_hs_hue is not None and _spec.dp_hs_saturation is not None
    ) or _spec.dp_colour_data is not None
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

    entity._optimistic_state = None  # set by __init__ normally
    entity.async_write_ha_state = MagicMock()  # stub out HA framework call
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
        light.async_write_ha_state = MagicMock()  # stub out HA framework call
        # Should not raise — just logs (no dp_scene configured)
        await light.async_turn_on(**{ATTR_EFFECT: "rainbow"})
        coord.async_send_dps.assert_called_once()


# ── Effect DP mapping ──────────────────────────────────────────────────────────


class TestLightEffectDP:
    """AC1 + AC2: effect_list and dp_scene DP mapping."""

    def test_effect_list_property(self) -> None:
        """AC1: effect_list returns list of effect names from spec."""
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1", "scene_2", "scene_3"))
        light = TuyaCloudlessLight(coord, spec)

        assert light.effect_list == ["scene_1", "scene_2", "scene_3"]

    def test_effect_property_reads_scene_dp(self) -> None:
        """AC1: effect property returns current scene DP value."""
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"25": "scene_2"})
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1", "scene_2"))
        light = TuyaCloudlessLight(coord, spec)

        assert light.effect == "scene_2"

    def test_effect_property_none_when_dp_missing(self) -> None:
        """effect returns None when scene DP not yet reported by device."""
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({})
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1",))
        light = TuyaCloudlessLight(coord, spec)

        assert light.effect is None

    def test_effect_property_none_when_no_dp_scene(self) -> None:
        """effect returns None when no dp_scene is configured in spec."""
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"25": "scene_1"})
        spec = _make_light_spec(effects=("scene_1",))  # no dp_scene
        light = TuyaCloudlessLight(coord, spec)

        assert light.effect is None

    @pytest.mark.asyncio
    async def test_turn_on_with_known_effect_sends_scene_dp(self) -> None:
        """AC2: async_turn_on(effect=X) sends effect name to dp_scene."""
        from homeassistant.components.light import ATTR_EFFECT

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1", "scene_2"))
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_EFFECT: "scene_1"})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert call_dps.get("25") == "scene_1"

    @pytest.mark.asyncio
    async def test_turn_on_second_effect_sends_correct_value(self) -> None:
        """AC3: each effect correctly mapped — test scene_2."""
        from homeassistant.components.light import ATTR_EFFECT

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1", "scene_2", "scene_3"))
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_EFFECT: "scene_2"})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert call_dps.get("25") == "scene_2"

    @pytest.mark.asyncio
    async def test_turn_on_third_effect_sends_correct_value(self) -> None:
        """AC3: each effect correctly mapped — test scene_3."""
        from homeassistant.components.light import ATTR_EFFECT

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1", "scene_2", "scene_3"))
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_EFFECT: "scene_3"})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert call_dps.get("25") == "scene_3"

    @pytest.mark.asyncio
    async def test_turn_on_unknown_effect_not_sent(self) -> None:
        """Unknown effect name must NOT be sent to the device."""
        from homeassistant.components.light import ATTR_EFFECT

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1",))
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_EFFECT: "unknown_effect"})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert "25" not in call_dps

    @pytest.mark.asyncio
    async def test_effect_includes_power_on(self) -> None:
        """Turning on with an effect also powers the light on."""
        from homeassistant.components.light import ATTR_EFFECT

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator()
        spec = _make_light_spec(dp_scene_id="25", effects=("scene_1",))
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_EFFECT: "scene_1"})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert call_dps.get("1") is True
        assert call_dps.get("25") == "scene_1"


# ── colour_data DP (HSV) ───────────────────────────────────────────────────────


class TestColourDataHelpers:
    """Unit tests for _encode_colour_data and _decode_colour_data helpers."""

    def test_encode_full_brightness_red(self) -> None:
        from custom_components.tuya_cloudless.light import _encode_colour_data

        # Red: hue=0, sat=100%, bri=255
        result = _encode_colour_data(0.0, 100.0, 255)
        assert len(result) == 12
        assert result == "000003e803e8"

    def test_encode_mid_hue(self) -> None:
        from custom_components.tuya_cloudless.light import _encode_colour_data

        # Hue 180 (cyan), sat=100%, bri=255
        result = _encode_colour_data(180.0, 100.0, 255)
        assert result == "00b403e803e8"

    def test_encode_half_brightness(self) -> None:
        from custom_components.tuya_cloudless.light import _encode_colour_data

        result = _encode_colour_data(180.0, 100.0, 128)
        assert len(result) == 12
        # V component should be roughly half of 1000
        v = int(result[8:12], 16)
        assert 490 <= v <= 510

    def test_decode_matches_encode(self) -> None:
        from custom_components.tuya_cloudless.light import _decode_colour_data, _encode_colour_data

        encoded = _encode_colour_data(180.0, 75.0, 200)
        decoded = _decode_colour_data(encoded)
        assert decoded is not None
        hue, sat, bri = decoded
        assert abs(hue - 180.0) < 1.0
        assert abs(sat - 75.0) < 1.0
        assert abs(bri - 200) <= 2  # rounding tolerance

    def test_decode_invalid_length(self) -> None:
        from custom_components.tuya_cloudless.light import _decode_colour_data

        assert _decode_colour_data("00b4") is None
        assert _decode_colour_data("") is None

    def test_decode_invalid_hex(self) -> None:
        from custom_components.tuya_cloudless.light import _decode_colour_data

        assert _decode_colour_data("GGGGSSSSBBBB") is None

    def test_decode_non_string(self) -> None:
        from custom_components.tuya_cloudless.light import _decode_colour_data

        assert _decode_colour_data(12345) is None  # type: ignore[arg-type]


class TestLightColourDataDP:
    """Integration tests for dp_colour_data in TuyaCloudlessLight."""

    def test_hs_color_reads_from_colour_data_dp(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"5": "00b403e803e8"})  # hue=180, sat=100%, bri=255
        spec = _make_light_spec(
            dp_brightness_id=None,
            dp_color_temp_id=None,
            dp_colour_data_id="5",
        )
        light = TuyaCloudlessLight(coord, spec)

        hs = light.hs_color
        assert hs is not None
        hue, sat = hs
        assert abs(hue - 180.0) < 1.0
        assert abs(sat - 100.0) < 1.0

    def test_hs_color_none_when_dp_missing(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({})
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None, dp_colour_data_id="5")
        light = TuyaCloudlessLight(coord, spec)

        assert light.hs_color is None

    def test_brightness_reads_v_from_colour_data(self) -> None:
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        # "00b403e801f4": hue=180, sat=100%, V=500/1000 → bri≈127
        coord = _make_coordinator({"5": "00b403e801f4"})
        spec = _make_light_spec(
            dp_brightness_id=None,
            dp_color_temp_id=None,
            dp_colour_data_id="5",
        )
        light = TuyaCloudlessLight(coord, spec)

        bri = light.brightness
        assert bri is not None
        assert 120 <= bri <= 135

    def test_colour_data_hs_mode_detected(self) -> None:
        from homeassistant.components.light import ColorMode

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"5": "00b403e803e8"})
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None, dp_colour_data_id="5")
        light = TuyaCloudlessLight(coord, spec)

        assert ColorMode.HS in light._attr_supported_color_modes

    @pytest.mark.asyncio
    async def test_turn_on_hs_via_colour_data(self) -> None:
        from homeassistant.components.light import ATTR_HS_COLOR

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({"5": "00b403e803e8"})
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None, dp_colour_data_id="5")
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_HS_COLOR: (180.0, 100.0)})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert "5" in call_dps
        encoded = call_dps["5"]
        assert len(encoded) == 12
        assert encoded.startswith("00b4")  # hue=180

    @pytest.mark.asyncio
    async def test_turn_on_hs_and_brightness_via_colour_data(self) -> None:
        """When HS + brightness both provided, brightness folded into V component."""
        from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_HS_COLOR

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({})
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None, dp_colour_data_id="5")
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_HS_COLOR: (120.0, 80.0), ATTR_BRIGHTNESS: 128})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert "5" in call_dps
        encoded = call_dps["5"]
        # V component ≈ 128/255 * 1000 ≈ 502
        v = int(encoded[8:12], 16)
        assert 490 <= v <= 515

    @pytest.mark.asyncio
    async def test_turn_on_brightness_only_updates_v_in_colour_mode(self) -> None:
        """Standalone brightness change updates V in colour_data when in HS mode."""
        from homeassistant.components.light import ATTR_BRIGHTNESS

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        # Device currently in colour mode with hue=180, sat=100%
        coord = _make_coordinator({"5": "00b403e803e8"})
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None, dp_colour_data_id="5")
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert "5" in call_dps
        encoded = call_dps["5"]
        # Hue and sat should be preserved from current state
        assert encoded.startswith("00b4")
        v = int(encoded[8:12], 16)
        assert 490 <= v <= 515

    @pytest.mark.asyncio
    async def test_colour_data_sets_color_mode_dp(self) -> None:
        """When dp_color_mode is present, setting HS via colour_data sends 'colour'."""
        from homeassistant.components.light import ATTR_HS_COLOR

        from custom_components.tuya_cloudless.light import TuyaCloudlessLight

        coord = _make_coordinator({})
        spec = _make_light_spec(
            dp_brightness_id=None,
            dp_color_temp_id=None,
            dp_colour_data_id="5",
            dp_color_mode_id="2",
        )
        light = TuyaCloudlessLight(coord, spec)
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_HS_COLOR: (180.0, 100.0)})

        call_dps = coord.async_send_dps.call_args[0][0]
        assert call_dps.get("2") == "colour"
