"""Tests for custom_components.tuya_cloudless.light."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.light import (
    TuyaCloudlessLight,
    _ha_to_tuya_brightness,
    _tuya_to_ha_brightness,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(
    dps: dict[str, Any] | None = None,
    gw_id: str = "gw001",
) -> MagicMock:
    coord = MagicMock()
    coord._gw_id = gw_id
    coord.gw_id = gw_id
    coord.device_name = gw_id
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = DeviceState(available=True, dps=dps or {})
    coord.async_send_dps = AsyncMock()
    return coord


def _make_light_spec(
    dp_power_id: str = "1",
    dp_brightness_id: str | None = "2",
    dp_color_temp_id: str | None = "3",
    dp_hs_hue_id: str | None = None,
    dp_hs_sat_id: str | None = None,
    dp_color_mode_id: str | None = None,
    effects: tuple[str, ...] = (),
) -> EntitySpec:
    return EntitySpec(
        platform="light",
        name="main_light",
        dp_power=DPSpec(id=dp_power_id, type="bool"),
        dp_brightness=DPSpec(id=dp_brightness_id, type="int", min_raw=10, max_raw=1000)
        if dp_brightness_id
        else None,
        dp_color_temp=DPSpec(id=dp_color_temp_id, type="int", min_raw=0, max_raw=1000)
        if dp_color_temp_id
        else None,
        dp_hs_hue=DPSpec(id=dp_hs_hue_id, type="int", max_raw=360) if dp_hs_hue_id else None,
        dp_hs_saturation=DPSpec(id=dp_hs_sat_id, type="int", max_raw=1000)
        if dp_hs_sat_id
        else None,
        dp_color_mode=DPSpec(id=dp_color_mode_id, type="enum") if dp_color_mode_id else None,
        effects=effects,
    )


def _make_light(
    dps: dict[str, Any] | None = None,
    spec: EntitySpec | None = None,
    gw_id: str = "gw001",
) -> TuyaCloudlessLight:
    coord = _make_coordinator(dps, gw_id)
    _spec = spec or _make_light_spec()
    entity = TuyaCloudlessLight.__new__(TuyaCloudlessLight)
    entity.coordinator = coord
    entity._dp_id = _spec.dp_power.id if _spec.dp_power else "1"
    entity._spec = _spec
    entity._attr_unique_id = f"{gw_id}_{_spec.platform}_{_spec.name}"
    entity._attr_translation_key = _spec.name
    entity._attr_min_color_temp_kelvin = 2700
    entity._attr_max_color_temp_kelvin = 6500
    # Determine supported colour modes
    has_hs = _spec.dp_hs_hue is not None and _spec.dp_hs_saturation is not None
    has_ct = _spec.dp_color_temp is not None
    has_bri = _spec.dp_brightness is not None
    supported: set[ColorMode] = set()
    if has_hs:
        supported.add(ColorMode.HS)
    if has_ct:
        supported.add(ColorMode.COLOR_TEMP)
    if not supported and has_bri:
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


# ── Brightness helpers ─────────────────────────────────────────────────────────


class TestBrightnessHelpers:
    def test_tuya_to_ha_min(self) -> None:
        assert _tuya_to_ha_brightness(10, 10, 1000) == 0

    def test_tuya_to_ha_max(self) -> None:
        assert _tuya_to_ha_brightness(1000, 10, 1000) == 255

    def test_tuya_to_ha_mid(self) -> None:
        result = _tuya_to_ha_brightness(505, 10, 1000)
        assert 125 <= result <= 130

    def test_ha_to_tuya_max(self) -> None:
        assert _ha_to_tuya_brightness(255, 10, 1000) == 1000

    def test_ha_to_tuya_min(self) -> None:
        assert _ha_to_tuya_brightness(0, 10, 1000) == 10

    def test_tuya_to_ha_zero_span(self) -> None:
        assert _tuya_to_ha_brightness(0, 0, 0) == 255


# ── Light on/off ───────────────────────────────────────────────────────────────


class TestLightOnOff:
    def test_is_on_true(self) -> None:
        e = _make_light({"1": True})
        assert e.is_on is True

    def test_is_on_false(self) -> None:
        e = _make_light({"1": False})
        assert e.is_on is False

    def test_is_on_none(self) -> None:
        e = _make_light({})
        assert e.is_on is None

    @pytest.mark.asyncio
    async def test_turn_on_basic(self) -> None:
        e = _make_light({"1": False})
        await e.async_turn_on()
        e.coordinator.async_send_dps.assert_awaited_once()
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert dps["1"] is True

    @pytest.mark.asyncio
    async def test_turn_off(self) -> None:
        e = _make_light({"1": True})
        await e.async_turn_off()
        e.coordinator.async_send_dps.assert_awaited_once()
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert dps["1"] is False


# ── Color modes ────────────────────────────────────────────────────────────────


class TestColorModes:
    def test_brightness_and_color_temp_modes(self) -> None:
        e = _make_light()
        modes = e._attr_supported_color_modes
        assert ColorMode.COLOR_TEMP in modes

    def test_onoff_mode_only(self) -> None:
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert ColorMode.ONOFF in e._attr_supported_color_modes

    def test_brightness_only_mode(self) -> None:
        spec = _make_light_spec(dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert ColorMode.BRIGHTNESS in e._attr_supported_color_modes

    def test_hs_mode(self) -> None:
        spec = _make_light_spec(dp_hs_hue_id="4", dp_hs_sat_id="5")
        e = _make_light(spec=spec)
        assert ColorMode.HS in e._attr_supported_color_modes

    def test_color_mode_from_dp_white(self) -> None:
        spec = _make_light_spec(
            dp_hs_hue_id="4",
            dp_hs_sat_id="5",
            dp_color_mode_id="6",
        )
        e = _make_light({"6": "white"}, spec=spec)
        assert e.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_from_dp_colour(self) -> None:
        spec = _make_light_spec(
            dp_hs_hue_id="4",
            dp_hs_sat_id="5",
            dp_color_mode_id="6",
        )
        e = _make_light({"6": "colour"}, spec=spec)
        assert e.color_mode == ColorMode.HS

    def test_color_mode_single_supported(self) -> None:
        spec = _make_light_spec(dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert e.color_mode == ColorMode.BRIGHTNESS

    def test_color_mode_onoff(self) -> None:
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert e.color_mode == ColorMode.ONOFF


# ── Brightness ─────────────────────────────────────────────────────────────────


class TestBrightness:
    def test_brightness_value(self) -> None:
        e = _make_light({"2": 505})
        bri = e.brightness
        assert bri is not None
        assert 125 <= bri <= 130

    def test_brightness_none_when_missing(self) -> None:
        e = _make_light({})
        assert e.brightness is None

    def test_brightness_none_when_no_spec(self) -> None:
        spec = _make_light_spec(dp_brightness_id=None)
        e = _make_light(spec=spec)
        assert e.brightness is None

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness(self) -> None:
        e = _make_light({"1": False, "2": 10})
        await e.async_turn_on(**{ATTR_BRIGHTNESS: 128})
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert "2" in dps
        assert dps["1"] is True


# ── Color temperature ──────────────────────────────────────────────────────────


class TestColorTemp:
    def test_color_temp_kelvin_min(self) -> None:
        e = _make_light({"3": 0})
        assert e.color_temp_kelvin == 2700

    def test_color_temp_kelvin_max(self) -> None:
        e = _make_light({"3": 1000})
        assert e.color_temp_kelvin == 6500

    def test_color_temp_none_when_missing(self) -> None:
        e = _make_light({})
        assert e.color_temp_kelvin is None

    def test_color_temp_none_when_no_spec(self) -> None:
        spec = _make_light_spec(dp_color_temp_id=None)
        e = _make_light(spec=spec)
        assert e.color_temp_kelvin is None

    @pytest.mark.asyncio
    async def test_turn_on_with_color_temp(self) -> None:
        e = _make_light({"1": False, "3": 0})
        await e.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4600})
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert "3" in dps
        assert dps["1"] is True


# ── HS color ───────────────────────────────────────────────────────────────────


class TestHSColor:
    def _make_hs_light(self, dps: dict[str, Any] | None = None) -> TuyaCloudlessLight:
        spec = _make_light_spec(
            dp_hs_hue_id="4",
            dp_hs_sat_id="5",
            dp_color_mode_id="6",
        )
        return _make_light(dps, spec=spec)

    def test_hs_color_values(self) -> None:
        e = self._make_hs_light({"4": 180, "5": 500})
        hs = e.hs_color
        assert hs is not None
        assert hs[0] == pytest.approx(180.0, abs=1)
        assert hs[1] == pytest.approx(50.0, abs=1)

    def test_hs_color_none_when_missing(self) -> None:
        e = self._make_hs_light({})
        assert e.hs_color is None

    def test_hs_color_none_when_no_spec(self) -> None:
        e = _make_light()  # No HS spec
        assert e.hs_color is None

    @pytest.mark.asyncio
    async def test_turn_on_with_hs_color(self) -> None:
        e = self._make_hs_light({"1": False})
        await e.async_turn_on(**{ATTR_HS_COLOR: (120.0, 75.0)})
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert "4" in dps  # hue
        assert "5" in dps  # saturation
        assert "6" in dps  # color mode
        assert dps["6"] == "colour"

    @pytest.mark.asyncio
    async def test_turn_on_with_color_temp_sets_mode(self) -> None:
        e = self._make_hs_light({"1": False, "3": 500})
        # This light has color_temp spec from base
        e._spec = _make_light_spec(
            dp_hs_hue_id="4",
            dp_hs_sat_id="5",
            dp_color_mode_id="6",
            dp_color_temp_id="3",
        )
        await e.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert "6" in dps
        assert dps["6"] == "white"


# ── Effects ────────────────────────────────────────────────────────────────────


class TestEffects:
    def test_effect_list_none_when_no_effects(self) -> None:
        e = _make_light()
        assert e._attr_effect_list is None
        assert e._attr_supported_features == LightEntityFeature(0)

    def test_effect_list_from_spec(self) -> None:
        spec = _make_light_spec(effects=("rainbow", "strobe"))
        e = _make_light(spec=spec)
        assert e._attr_effect_list == ["rainbow", "strobe"]
        assert LightEntityFeature.EFFECT in e._attr_supported_features

    def test_effect_property_returns_none(self) -> None:
        e = _make_light()
        assert e.effect is None


# ── Setup entry ────────────────────────────────────────────────────────────────


class TestLightSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.light import async_setup_entry

        coord = _make_coordinator({"1": True})
        spec = _make_light_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Generic Light",
        )

        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessLight)

    @pytest.mark.asyncio
    async def test_setup_no_light_specs_skips(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.light import async_setup_entry

        coord = _make_coordinator()
        switch_spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[switch_spec],
            profile_name="Test",
        )

        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 0


# ── Helper: run the real async_added_to_hass with a mocked last state ──────────


async def _run_restore(entity: TuyaCloudlessLight, state_str: str | None) -> None:
    mock_state = MagicMock() if state_str is not None else None
    if mock_state is not None:
        mock_state.state = state_str
        mock_state.attributes = {}
    entity.async_get_last_state = AsyncMock(return_value=mock_state)
    with patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()):
        await entity.async_added_to_hass()


# ── Restore state (lines 190-192) ──────────────────────────────────────────────


class TestLightRestoreState:
    """Tests for async_added_to_hass restore logic (lines 189-192)."""

    @pytest.mark.asyncio
    async def test_restore_state_on_sets_optimistic_true(self) -> None:
        """STATE_ON → _optimistic_state=True."""
        from homeassistant.const import STATE_ON

        e = _make_light({})
        e._restored_state = None
        await _run_restore(e, STATE_ON)
        assert e._optimistic_state is True

    @pytest.mark.asyncio
    async def test_restore_state_off_sets_optimistic_false(self) -> None:
        """STATE_OFF → _optimistic_state=False."""
        from homeassistant.const import STATE_OFF

        e = _make_light({})
        e._restored_state = None
        await _run_restore(e, STATE_OFF)
        assert e._optimistic_state is False

    @pytest.mark.asyncio
    async def test_restore_state_unavailable_does_not_set_optimistic(self) -> None:
        """'unavailable' must not set _optimistic_state."""
        e = _make_light({})
        e._optimistic_state = None
        e._restored_state = None
        await _run_restore(e, "unavailable")
        assert e._optimistic_state is None

    @pytest.mark.asyncio
    async def test_restore_no_prior_state_keeps_optimistic_none(self) -> None:
        """No prior state → _optimistic_state stays None."""
        e = _make_light({})
        e._optimistic_state = None
        e._restored_state = None
        await _run_restore(e, None)
        assert e._optimistic_state is None


# ── _handle_coordinator_update (lines 197-198) ─────────────────────────────────


class TestLightHandleCoordinatorUpdate:
    """Tests for _handle_coordinator_update (lines 194-198)."""

    def test_coordinator_update_clears_optimistic_state(self) -> None:
        """_handle_coordinator_update must clear _optimistic_state and call super."""
        e = _make_light({"1": True})
        e._optimistic_state = True

        with patch.object(CoordinatorEntity, "_handle_coordinator_update") as mock_super:
            e._handle_coordinator_update()

        assert e._optimistic_state is None
        mock_super.assert_called_once()


# ── color_mode fallback paths (lines 229-231) ──────────────────────────────────


class TestColorModeFallbacks:
    """Tests for color_mode property fallback paths (lines 224-231)."""

    def test_color_mode_color_temp_only_no_hs(self) -> None:
        """When only COLOR_TEMP is supported (no HS), color_mode returns COLOR_TEMP."""
        # Create a light with dp_color_temp but NO hs support and NO dp_color_mode
        spec = _make_light_spec(
            dp_color_temp_id="3",
            dp_brightness_id=None,
            dp_hs_hue_id=None,
            dp_hs_sat_id=None,
            dp_color_mode_id=None,
        )
        e = _make_light({}, spec=spec)
        # Supported modes will only contain COLOR_TEMP (because no HS)
        assert ColorMode.COLOR_TEMP in e._attr_supported_color_modes
        assert ColorMode.HS not in e._attr_supported_color_modes
        # len(modes) > 1 only if we force it; single mode returns via the len==1 branch
        # Force multiple modes to hit the ColorMode.COLOR_TEMP branch
        e._attr_supported_color_modes = frozenset({ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS})
        assert e.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_onoff_when_no_hs_no_ct(self) -> None:
        """When neither HS nor COLOR_TEMP is in supported modes, returns ONOFF (line 231)."""
        spec = _make_light_spec(dp_brightness_id=None, dp_color_temp_id=None)
        e = _make_light({}, spec=spec)
        # Force supported_color_modes to multiple modes that include neither HS nor COLOR_TEMP
        e._attr_supported_color_modes = frozenset({ColorMode.ONOFF, ColorMode.BRIGHTNESS})
        assert e.color_mode == ColorMode.ONOFF


# ── async_turn_on with brightness in HS colour mode + dp_color_mode (line 391) ─


class TestTurnOnBrightnessWithColorMode:
    """Tests for async_turn_on standalone brightness in colour mode (line 390-391)."""

    @pytest.mark.asyncio
    async def test_turn_on_brightness_in_hs_mode_sets_color_mode_dp(self) -> None:
        """Brightness in colour mode with dp_colour_data+dp_color_mode sets colour mode DP."""
        spec = EntitySpec(
            platform="light",
            name="main_light",
            dp_power=DPSpec(id="1", type="bool"),
            dp_brightness=DPSpec(id="2", type="int", min_raw=10, max_raw=1000),
            dp_colour_data=DPSpec(id="5", type="str"),
            dp_color_mode=DPSpec(id="6", type="enum"),
        )
        # Put HS data in DPs so hs_color is non-None and color_mode returns HS
        # colour_data format: "HHHHSSSSVVVV" (hex strings)
        # 120 degrees hue = 0x0078, 50% sat = 0x01F4 (max 1000 = 0x03E8 → 500 for 50%),
        # brightness 128 HA → tuya ~(128/255)*(990)+10 ≈ 507 → 0x01FB
        colour_data_val = "007801f401fb"
        dps: dict[str, Any] = {"1": True, "5": colour_data_val, "6": "colour"}
        coord = _make_coordinator(dps)
        entity = TuyaCloudlessLight.__new__(TuyaCloudlessLight)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = "gw001_light_main_light"
        entity._attr_translation_key = "main_light"
        entity._attr_min_color_temp_kelvin = 2700
        entity._attr_max_color_temp_kelvin = 6500
        entity._attr_supported_color_modes = frozenset({ColorMode.HS})
        entity._attr_effect_list = None
        entity._attr_supported_features = LightEntityFeature(0)
        entity._optimistic_state = None
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_on(**{ATTR_BRIGHTNESS: 128})

        dps_sent = entity.coordinator.async_send_dps.call_args[0][0]
        # dp_color_mode should be set to "colour"
        assert "6" in dps_sent
        assert dps_sent["6"] == "colour"


class TestLightIntGuards:
    """R20-3: brightness/hs_color/color_temp must return None on non-numeric DP values."""

    def test_brightness_none_on_non_numeric_dp(self) -> None:
        """brightness_pct returns None when device sends non-numeric brightness value."""
        e = _make_light({"2": "not-a-number"})
        assert e.brightness is None

    def test_hs_color_none_on_non_numeric_hue(self) -> None:
        """hs_color returns None when hue DP is non-numeric."""
        spec = _make_light_spec(dp_hs_hue_id="4", dp_hs_sat_id="5")
        e = _make_light({"4": "bad", "5": 500}, spec=spec)
        assert e.hs_color is None

    def test_hs_color_none_on_non_numeric_sat(self) -> None:
        """hs_color returns None when saturation DP is non-numeric."""
        spec = _make_light_spec(dp_hs_hue_id="4", dp_hs_sat_id="5")
        e = _make_light({"4": 180, "5": "bad"}, spec=spec)
        assert e.hs_color is None

    def test_color_temp_none_on_non_numeric_dp(self) -> None:
        """color_temp_kelvin returns None when device sends non-numeric value."""
        e = _make_light({"3": "bad"})
        assert e.color_temp_kelvin is None
