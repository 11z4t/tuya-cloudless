"""Tests for custom_components.tuya_cloudless.climate."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.climate import TuyaCloudlessClimate
from custom_components.tuya_cloudless.coordinator import DeviceState

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


def _make_climate_spec(
    scale: float = 0.1,
    options: tuple[str, ...] = ("heat", "cool", "auto"),
    target_min: float = 10.0,
    target_max: float = 30.0,
    step: float = 0.5,
) -> EntitySpec:
    return EntitySpec(
        platform="climate",
        name="thermostat",
        dp_power=DPSpec(id="1", type="bool"),
        dp_mode=DPSpec(id="2", type="enum"),
        dp_temp_set=DPSpec(id="3", type="int", min_raw=100, max_raw=300, scale=scale),
        dp_temp_current=DPSpec(id="4", type="int", scale=scale),
        dp_options=options,
        target_min=target_min,
        target_max=target_max,
        step=step,
    )


def _make_climate(
    dps: dict[str, Any] | None = None,
    scale: float = 10.0,
    options: tuple[str, ...] = ("heat", "cool", "auto"),
    gw_id: str = "gw001",
) -> TuyaCloudlessClimate:
    coord = _make_coordinator(dps, gw_id)
    spec = _make_climate_spec(scale=scale, options=options)
    entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
    entity.coordinator = coord
    entity._dp_id = "1"
    entity._spec = spec
    entity._attr_unique_id = f"{gw_id}_climate_thermostat"
    entity._attr_translation_key = "thermostat"
    # Compute hvac_modes same as __init__
    from custom_components.tuya_cloudless.climate import _TUYA_TO_HA_MODE

    ha_modes: list[HVACMode] = [HVACMode.OFF]
    for tm in options:
        hm = _TUYA_TO_HA_MODE.get(tm)
        if hm is not None and hm != HVACMode.OFF and hm not in ha_modes:
            ha_modes.append(hm)
    entity._attr_hvac_modes = ha_modes
    entity._tuya_options = list(options)
    entity._attr_min_temp = 10.0
    entity._attr_max_temp = 30.0
    entity._attr_target_temperature_step = 0.5
    features = (
        ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TARGET_TEMPERATURE
    )
    entity._attr_supported_features = features
    # Optimistic state (set by __init__ normally)
    entity._optimistic_hvac_mode = None
    entity._optimistic_target_temp = None
    entity._restored_state = None
    entity.async_write_ha_state = MagicMock()
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestTuyaCloudlessClimate:
    def test_unique_id_format(self) -> None:
        e = _make_climate()
        assert e._attr_unique_id == "gw001_climate_thermostat"

    def test_hvac_mode_off_when_power_false(self) -> None:
        e = _make_climate({"1": False, "2": "heat"})
        assert e.hvac_mode == HVACMode.OFF

    def test_hvac_mode_heat_when_mode_heat(self) -> None:
        e = _make_climate({"1": True, "2": "heat"})
        assert e.hvac_mode == HVACMode.HEAT

    def test_hvac_mode_cool_when_mode_cool(self) -> None:
        e = _make_climate({"1": True, "2": "cool"})
        assert e.hvac_mode == HVACMode.COOL

    def test_hvac_mode_auto_when_mode_auto(self) -> None:
        e = _make_climate({"1": True, "2": "auto"})
        assert e.hvac_mode == HVACMode.AUTO

    def test_hvac_mode_defaults_auto_when_no_mode_dp(self) -> None:
        e = _make_climate({"1": True})
        # With no mode DP value, fallback to AUTO (neutral) when on
        assert e.hvac_mode == HVACMode.AUTO

    def test_hvac_mode_none_when_power_missing(self) -> None:
        e = _make_climate({})
        assert e.hvac_mode is None

    def test_current_temperature(self) -> None:
        e = _make_climate({"4": 220}, scale=0.1)
        assert e.current_temperature == pytest.approx(22.0)

    def test_target_temperature(self) -> None:
        e = _make_climate({"3": 225}, scale=0.1)
        assert e.target_temperature == pytest.approx(22.5)

    def test_target_temperature_none_when_missing(self) -> None:
        e = _make_climate({})
        assert e.target_temperature is None

    @pytest.mark.asyncio
    async def test_set_temperature_scale_point1(self) -> None:
        e = _make_climate({"1": True}, scale=0.1)
        await e.async_set_temperature(**{ATTR_TEMPERATURE: 22.0})
        e.coordinator.async_send_dps.assert_awaited_once_with({"3": 220})

    @pytest.mark.asyncio
    async def test_set_hvac_mode_off(self) -> None:
        e = _make_climate({"1": True, "2": "heat"})
        await e.async_set_hvac_mode(HVACMode.OFF)
        e.coordinator.async_send_dps.assert_awaited_once_with({"1": False})

    @pytest.mark.asyncio
    async def test_set_hvac_mode_cool(self) -> None:
        e = _make_climate({"1": False})
        await e.async_set_hvac_mode(HVACMode.COOL)
        call_args = e.coordinator.async_send_dps.call_args[0][0]
        assert call_args["1"] is True
        assert call_args["2"] == "cool"

    @pytest.mark.asyncio
    async def test_set_hvac_mode_heat(self) -> None:
        e = _make_climate({"1": False})
        await e.async_set_hvac_mode(HVACMode.HEAT)
        call_args = e.coordinator.async_send_dps.call_args[0][0]
        assert call_args["1"] is True
        assert call_args["2"] == "heat"

    def test_supported_features_has_target_temperature(self) -> None:
        e = _make_climate()
        assert e._attr_supported_features & ClimateEntityFeature.TARGET_TEMPERATURE

    def test_hvac_modes_includes_off(self) -> None:
        e = _make_climate(options=("heat", "cool"))
        assert HVACMode.OFF in e._attr_hvac_modes
        assert HVACMode.HEAT in e._attr_hvac_modes
        assert HVACMode.COOL in e._attr_hvac_modes

    def test_manual_mode_maps_to_heat(self) -> None:
        e = _make_climate({"1": True, "2": "manual"}, options=("manual", "eco", "off"))
        assert e.hvac_mode == HVACMode.HEAT


class TestClimateSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.climate import async_setup_entry

        coord = _make_coordinator({"1": True})
        spec = _make_climate_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Smart Thermostat",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list[Any] = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessClimate)

    @pytest.mark.asyncio
    async def test_setup_skips_non_climate(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.climate import async_setup_entry

        coord = _make_coordinator()
        sensor_spec = EntitySpec(platform="sensor", name="power")
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[sensor_spec],
            profile_name="Test",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list[Any] = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 0


class TestClimateExtraStateAttributes:
    def test_all_dps_present(self) -> None:
        climate = _make_climate(dps={"1": True, "2": "heat", "3": 220, "4": 195})
        attrs = climate.extra_state_attributes
        assert attrs["dp_power_raw"] is True
        assert attrs["dp_mode_raw"] == "heat"
        assert attrs["dp_temp_set_raw"] == 220
        assert attrs["dp_temp_current_raw"] == 195

    def test_missing_dps_return_none(self) -> None:
        climate = _make_climate(dps={})
        attrs = climate.extra_state_attributes
        assert attrs["dp_power_raw"] is None
        assert attrs["dp_temp_set_raw"] is None

    def test_no_mode_dp_omits_key(self) -> None:
        coord = _make_coordinator(dps={"1": True})
        spec = EntitySpec(
            platform="climate",
            name="heater",
            dp_power=DPSpec(id="1", type="bool"),
        )
        from custom_components.tuya_cloudless.climate import TuyaCloudlessClimate

        climate = TuyaCloudlessClimate(coord, spec)
        attrs = climate.extra_state_attributes
        assert "dp_mode_raw" not in attrs
        assert "dp_temp_set_raw" not in attrs
        assert "dp_temp_current_raw" not in attrs


class TestClimateInit:
    """Test TuyaCloudlessClimate.__init__ attribute assignments via real constructor."""

    def _make(
        self,
        spec: EntitySpec | None = None,
        gw_id: str = "mydev",
        dps: dict[str, Any] | None = None,
    ) -> TuyaCloudlessClimate:
        from custom_components.tuya_cloudless.climate import TuyaCloudlessClimate

        coord = _make_coordinator(dps or {}, gw_id)
        return TuyaCloudlessClimate(coord, spec or _make_climate_spec())

    def test_unique_id_format(self) -> None:
        entity = self._make(gw_id="mydev")
        assert entity._attr_unique_id == "mydev_climate_thermostat"

    def test_hvac_modes_include_off(self) -> None:
        spec = _make_climate_spec(options=("heat", "cool"))
        entity = self._make(spec=spec)
        assert HVACMode.OFF in entity.hvac_modes
        assert HVACMode.HEAT in entity.hvac_modes
        assert HVACMode.COOL in entity.hvac_modes

    def test_min_max_from_target(self) -> None:
        spec = _make_climate_spec(target_min=5.0, target_max=40.0)
        entity = self._make(spec=spec)
        assert entity.min_temp == pytest.approx(5.0)
        assert entity.max_temp == pytest.approx(40.0)

    def test_min_max_from_dp_when_no_target(self) -> None:
        spec = EntitySpec(
            platform="climate",
            name="heater",
            dp_power=DPSpec(id="1", type="bool"),
            dp_temp_set=DPSpec(id="3", type="int", min_raw=50, max_raw=350, scale=0.1),
        )
        entity = self._make(spec=spec)
        assert entity.min_temp == pytest.approx(5.0)  # 50 * 0.1
        assert entity.max_temp == pytest.approx(35.0)  # 350 * 0.1

    def test_supported_features_with_temp(self) -> None:
        from homeassistant.components.climate import ClimateEntityFeature

        entity = self._make()
        assert ClimateEntityFeature.TARGET_TEMPERATURE in entity.supported_features
        assert ClimateEntityFeature.TURN_ON in entity.supported_features

    def test_supported_features_no_temp_set(self) -> None:
        from homeassistant.components.climate import ClimateEntityFeature

        spec = EntitySpec(platform="climate", name="heater", dp_power=DPSpec(id="1", type="bool"))
        entity = self._make(spec=spec)
        assert ClimateEntityFeature.TARGET_TEMPERATURE not in entity.supported_features


# ── Optimistic state tests ─────────────────────────────────────────────────────


class TestClimateOptimisticState:
    """Climate optimistic state: set before command, cleared by coordinator update."""

    @pytest.mark.asyncio
    async def test_set_hvac_mode_sets_optimistic_immediately(self) -> None:
        """async_set_hvac_mode sets optimistic mode before awaiting the send."""
        e = _make_climate({"1": False})
        await e.async_set_hvac_mode(HVACMode.HEAT)
        assert e._optimistic_hvac_mode == HVACMode.HEAT
        assert e.hvac_mode == HVACMode.HEAT
        e.async_write_ha_state.assert_called()

    @pytest.mark.asyncio
    async def test_set_hvac_mode_off_sets_optimistic_off(self) -> None:
        """async_set_hvac_mode(OFF) sets optimistic OFF immediately."""
        e = _make_climate({"1": True, "2": "heat"})
        await e.async_set_hvac_mode(HVACMode.OFF)
        assert e._optimistic_hvac_mode == HVACMode.OFF
        assert e.hvac_mode == HVACMode.OFF

    @pytest.mark.asyncio
    async def test_set_temperature_sets_optimistic_immediately(self) -> None:
        """async_set_temperature sets optimistic temp before awaiting the send."""
        e = _make_climate({"1": True, "3": 200}, scale=0.1)  # 200 * 0.1 = 20.0
        await e.async_set_temperature(**{ATTR_TEMPERATURE: 22.0})
        assert e._optimistic_target_temp == 22.0
        assert e.target_temperature == 22.0

    @pytest.mark.asyncio
    async def test_turn_on_sets_first_non_off_mode(self) -> None:
        """R42-F4: async_turn_on sets optimistic hvac_mode to first non-OFF mode."""
        # options=("heat", "cool", "auto") → first non-OFF is HEAT
        e = _make_climate({"1": False})
        await e.async_turn_on()
        assert e._optimistic_hvac_mode == HVACMode.HEAT
        assert e.hvac_mode == HVACMode.HEAT

    @pytest.mark.asyncio
    async def test_turn_on_sets_auto_when_only_auto(self) -> None:
        """R42-F4: async_turn_on uses AUTO when it is the only non-OFF mode."""
        e = _make_climate({"1": False}, options=("auto",))
        await e.async_turn_on()
        assert e._optimistic_hvac_mode == HVACMode.AUTO

    @pytest.mark.asyncio
    async def test_turn_on_falls_back_to_auto_when_no_modes(self) -> None:
        """R42-F4: async_turn_on falls back to AUTO when hvac_modes is empty."""
        e = _make_climate({"1": False}, options=())
        # _attr_hvac_modes = [OFF] only → non_off is empty → fallback AUTO
        await e.async_turn_on()
        assert e._optimistic_hvac_mode == HVACMode.AUTO

    @pytest.mark.asyncio
    async def test_turn_off_sets_optimistic_off(self) -> None:
        """async_turn_off sets optimistic hvac_mode to OFF immediately."""
        e = _make_climate({"1": True, "2": "heat"})
        await e.async_turn_off()
        assert e._optimistic_hvac_mode == HVACMode.OFF

    @pytest.mark.asyncio
    async def test_set_hvac_mode_reverts_on_error(self) -> None:
        """async_set_hvac_mode reverts optimistic state on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_climate({"1": False})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("fail")
        with pytest.raises(HomeAssistantError):
            await e.async_set_hvac_mode(HVACMode.HEAT)
        assert e._optimistic_hvac_mode is None
        assert e.async_write_ha_state.call_count == 2

    @pytest.mark.asyncio
    async def test_set_temperature_reverts_on_error(self) -> None:
        """async_set_temperature reverts optimistic temp on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_climate({"1": True, "3": 200})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("fail")
        with pytest.raises(HomeAssistantError):
            await e.async_set_temperature(**{ATTR_TEMPERATURE: 22.0})
        assert e._optimistic_target_temp is None

    def test_optimistic_wins_over_stale_dp(self) -> None:
        """Optimistic takes priority over stale DP until coordinator clears it."""
        e = _make_climate({"1": False})  # DP says OFF
        e._optimistic_hvac_mode = HVACMode.HEAT
        assert e.hvac_mode == HVACMode.HEAT

    def test_coordinator_update_clears_optimistic(self) -> None:
        """After coordinator clears optimistic, live DP takes over."""
        e = _make_climate({"1": False})
        e._optimistic_hvac_mode = HVACMode.HEAT
        assert e.hvac_mode == HVACMode.HEAT
        e._optimistic_hvac_mode = None  # Cleared by coordinator update
        assert e.hvac_mode == HVACMode.OFF  # Live DP wins


# ── Restore state tests ────────────────────────────────────────────────────────


# Helper: run the real async_added_to_hass with a mocked last state.
async def _run_restore(
    entity: TuyaCloudlessClimate,
    state_str: str | None,
    attributes: dict[str, object] | None = None,
) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from homeassistant.helpers.update_coordinator import CoordinatorEntity

    mock_state = MagicMock() if state_str is not None else None
    if mock_state is not None:
        mock_state.state = state_str
        mock_state.attributes = attributes or {}
    entity.async_get_last_state = AsyncMock(return_value=mock_state)
    with patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()):
        await entity.async_added_to_hass()


class TestClimateRestoreState:
    @pytest.mark.asyncio
    async def test_restore_heat_mode(self) -> None:
        """Real async_added_to_hass: 'heat' → optimistic_hvac_mode=HVACMode.HEAT."""
        e = _make_climate({})
        await _run_restore(e, "heat")
        assert e._optimistic_hvac_mode == HVACMode.HEAT
        assert e.hvac_mode == HVACMode.HEAT

    @pytest.mark.asyncio
    async def test_restore_cool_mode(self) -> None:
        """Real async_added_to_hass: 'cool' → optimistic_hvac_mode=HVACMode.COOL."""
        e = _make_climate({}, options=("heat", "cool", "auto"))
        await _run_restore(e, "cool")
        assert e._optimistic_hvac_mode == HVACMode.COOL

    @pytest.mark.asyncio
    async def test_restore_off_mode(self) -> None:
        """Real async_added_to_hass: 'off' → optimistic_hvac_mode=HVACMode.OFF."""
        e = _make_climate({})
        await _run_restore(e, "off")
        assert e._optimistic_hvac_mode == HVACMode.OFF

    @pytest.mark.asyncio
    async def test_restore_auto_mode(self) -> None:
        """Real async_added_to_hass: 'auto' → optimistic_hvac_mode=HVACMode.AUTO."""
        e = _make_climate({})
        await _run_restore(e, "auto")
        assert e._optimistic_hvac_mode == HVACMode.AUTO

    @pytest.mark.asyncio
    async def test_restore_invalid_state_is_ignored(self) -> None:
        """Real async_added_to_hass: 'unavailable' must not touch optimistic."""
        e = _make_climate({})
        await _run_restore(e, "unavailable")
        assert e._optimistic_hvac_mode is None

    @pytest.mark.asyncio
    async def test_restore_none_last_state_is_ignored(self) -> None:
        """Real async_added_to_hass: no recorded state → optimistic untouched."""
        e = _make_climate({})
        await _run_restore(e, None)
        assert e._optimistic_hvac_mode is None

    @pytest.mark.asyncio
    async def test_restore_mode_not_in_supported_modes_ignored(self) -> None:
        """Real async_added_to_hass: valid HVACMode not in device's modes is ignored."""
        e = _make_climate({}, options=("heat",))  # only heat + off supported
        await _run_restore(e, "cool")
        assert e._optimistic_hvac_mode is None

    @pytest.mark.asyncio
    async def test_restore_sets_restored_state_attribute(self) -> None:
        """RestoreStateMixin must populate _restored_state from last HA state."""
        e = _make_climate({})
        await _run_restore(e, "heat")
        assert e._restored_state == "heat"

    @pytest.mark.asyncio
    async def test_restore_off_restored_state_attribute(self) -> None:
        """_restored_state is 'off' after restoring an off climate entity."""
        e = _make_climate({})
        await _run_restore(e, "off")
        assert e._restored_state == "off"

    def test_restored_state_initialised_to_none(self) -> None:
        """_restored_state must start as None before async_added_to_hass runs."""
        e = _make_climate({})
        assert e._restored_state is None


# ── Restore extra-data tests (PLAT-718: target temperature) ───────────────────


class TestClimateRestoreExtraData:
    @pytest.mark.asyncio
    async def test_restore_target_temperature_from_attributes(self) -> None:
        """Target temperature is restored from saved attributes."""
        e = _make_climate({})
        await _run_restore(e, "heat", attributes={"temperature": 22.5})
        assert e._optimistic_target_temp == 22.5
        assert e.target_temperature == 22.5

    @pytest.mark.asyncio
    async def test_restore_target_temperature_integer(self) -> None:
        """Integer temperature attribute is cast to float."""
        e = _make_climate({})
        await _run_restore(e, "heat", attributes={"temperature": 20})
        assert e._optimistic_target_temp == 20.0

    @pytest.mark.asyncio
    async def test_restore_target_temperature_none_when_no_dp_temp_set(self) -> None:
        """Target temperature is not restored when dp_temp_set is absent."""
        spec = EntitySpec(
            platform="climate",
            name="thermostat",
            dp_power=DPSpec(id="1", type="bool"),
            dp_options=("heat",),
        )
        from custom_components.tuya_cloudless.coordinator import DeviceState

        coord = MagicMock()
        coord.gw_id = "gw001"
        coord.device_name = "gw001"
        coord.profile_name = ""
        coord.version = "3.3"
        coord.state = DeviceState(available=True, dps={})
        coord.async_send_dps = AsyncMock()
        coord.device_info = MagicMock()
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_thermostat"
        entity._attr_translation_key = "thermostat"
        entity._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        entity._tuya_options = ["heat"]
        entity._attr_min_temp = 10.0
        entity._attr_max_temp = 30.0
        entity._attr_target_temperature_step = 0.5
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()
        await _run_restore(entity, "heat", attributes={"temperature": 21.0})
        assert entity._optimistic_target_temp is None

    @pytest.mark.asyncio
    async def test_restore_target_temperature_invalid_ignored(self) -> None:
        """Non-numeric temperature attribute must not crash."""
        e = _make_climate({})
        await _run_restore(e, "heat", attributes={"temperature": "bad"})
        assert e._optimistic_target_temp is None

    @pytest.mark.asyncio
    async def test_restore_temperature_without_state(self) -> None:
        """Temperature can be restored even if state is None (no last state)."""
        e = _make_climate({})
        # When no last state exists, temperature stays None
        await _run_restore(e, None)
        assert e._optimistic_target_temp is None

    @pytest.mark.asyncio
    async def test_restore_temperature_with_off_mode(self) -> None:
        """Temperature is restored alongside OFF hvac_mode."""
        e = _make_climate({})
        await _run_restore(e, "off", attributes={"temperature": 18.0})
        assert e._optimistic_hvac_mode == HVACMode.OFF
        assert e._optimistic_target_temp == 18.0

    @pytest.mark.asyncio
    async def test_restore_temperature_absent_attribute_is_ignored(self) -> None:
        """Missing temperature key in attributes must not change optimistic_target_temp."""
        e = _make_climate({})
        await _run_restore(e, "heat", attributes={})
        assert e._optimistic_target_temp is None

    @pytest.mark.asyncio
    async def test_restore_temperature_wins_before_coordinator_update(self) -> None:
        """Restored target_temperature takes priority until coordinator clears it."""
        e = _make_climate({})
        await _run_restore(e, "heat", attributes={"temperature": 24.0})
        assert e.target_temperature == 24.0
        # Coordinator update clears optimistic
        e._optimistic_target_temp = None
        assert e.target_temperature is None  # live DP (missing) takes over


# ── _handle_coordinator_update ────────────────────────────────────────────────


class TestHandleCoordinatorUpdate:
    def test_clears_optimistic_hvac_mode(self) -> None:
        """_handle_coordinator_update must clear _optimistic_hvac_mode."""
        from unittest.mock import patch

        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        e = _make_climate({"1": True, "2": "heat"})
        e._optimistic_hvac_mode = HVACMode.COOL
        e._optimistic_target_temp = 22.0

        # Patch parent _handle_coordinator_update to avoid side-effects
        with patch.object(CoordinatorEntity, "_handle_coordinator_update"):
            e._handle_coordinator_update()

        assert e._optimistic_hvac_mode is None
        assert e._optimistic_target_temp is None

    def test_clears_and_calls_super(self) -> None:
        """_handle_coordinator_update clears optimistic state then calls super."""
        from unittest.mock import MagicMock, patch

        from homeassistant.helpers.update_coordinator import CoordinatorEntity

        e = _make_climate({"1": True})
        e._optimistic_hvac_mode = HVACMode.AUTO

        parent_mock = MagicMock()
        with patch.object(CoordinatorEntity, "_handle_coordinator_update", parent_mock):
            e._handle_coordinator_update()

        assert e._optimistic_hvac_mode is None
        parent_mock.assert_called_once()


# ── hvac_mode with dp_power = None ────────────────────────────────────────────


class TestHvacModeNoDpPower:
    def test_hvac_mode_returns_none_when_dp_power_none(self) -> None:
        """hvac_mode returns None when spec has no dp_power."""
        from tuya_cloudless.profiles import EntitySpec

        coord = _make_coordinator(dps={"1": True})
        spec = EntitySpec(platform="climate", name="nopow")
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_nopow"
        entity._attr_translation_key = "nopow"
        entity._attr_hvac_modes = [HVACMode.OFF]
        entity._tuya_options = []
        entity._attr_min_temp = 7.0
        entity._attr_max_temp = 35.0
        entity._attr_target_temperature_step = 1.0
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()

        assert entity.hvac_mode is None


# ── current_temperature edge cases ────────────────────────────────────────────


class TestCurrentTemperatureEdgeCases:
    def test_current_temperature_none_when_no_dp_temp_current(self) -> None:
        """current_temperature returns None when dp_temp_current is None in spec."""
        from tuya_cloudless.profiles import DPSpec, EntitySpec

        coord = _make_coordinator(dps={"1": True})
        spec = EntitySpec(
            platform="climate",
            name="notemp",
            dp_power=DPSpec(id="1", type="bool"),
        )
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_notemp"
        entity._attr_translation_key = "notemp"
        entity._attr_hvac_modes = [HVACMode.OFF]
        entity._tuya_options = []
        entity._attr_min_temp = 7.0
        entity._attr_max_temp = 35.0
        entity._attr_target_temperature_step = 1.0
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()

        assert entity.current_temperature is None

    def test_current_temperature_none_when_raw_is_none(self) -> None:
        """current_temperature returns None when dp value is missing from coordinator."""
        e = _make_climate(dps={}, scale=0.1)  # DP "4" not in dps
        assert e.current_temperature is None


# ── target_temperature when dp_temp_set is None ───────────────────────────────


class TestTargetTemperatureNoDpTempSet:
    def test_target_temperature_none_when_no_dp_temp_set(self) -> None:
        """target_temperature returns None when spec has no dp_temp_set."""
        from tuya_cloudless.profiles import DPSpec, EntitySpec

        coord = _make_coordinator(dps={"1": True})
        spec = EntitySpec(
            platform="climate",
            name="heater",
            dp_power=DPSpec(id="1", type="bool"),
            # no dp_temp_set
        )
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_heater"
        entity._attr_translation_key = "heater"
        entity._attr_hvac_modes = [HVACMode.OFF]
        entity._tuya_options = []
        entity._attr_min_temp = 7.0
        entity._attr_max_temp = 35.0
        entity._attr_target_temperature_step = 1.0
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()

        assert entity.target_temperature is None


# ── async_set_temperature when dp_temp_set is None ────────────────────────────


class TestSetTemperatureNoDpTempSet:
    @pytest.mark.asyncio
    async def test_set_temperature_noop_when_no_dp_temp_set(self) -> None:
        """async_set_temperature returns immediately if spec.dp_temp_set is None."""
        from tuya_cloudless.profiles import DPSpec, EntitySpec

        coord = _make_coordinator(dps={"1": True})
        spec = EntitySpec(
            platform="climate",
            name="heater",
            dp_power=DPSpec(id="1", type="bool"),
        )
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_heater"
        entity._attr_translation_key = "heater"
        entity._attr_hvac_modes = [HVACMode.OFF]
        entity._tuya_options = []
        entity._attr_min_temp = 7.0
        entity._attr_max_temp = 35.0
        entity._attr_target_temperature_step = 1.0
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.0})
        entity.coordinator.async_send_dps.assert_not_awaited()


# ── async_set_temperature with scale=0 edge case ──────────────────────────────


class TestSetTemperatureScaleZero:
    @pytest.mark.asyncio
    async def test_set_temperature_scale_zero_is_ignored(self) -> None:
        """With scale=0, async_set_temperature returns early without sending.

        The guard prevents ZeroDivisionError from an invalid profile configuration.
        """
        e = _make_climate({"1": True, "3": 0}, scale=0.0)
        await e.async_set_temperature(**{ATTR_TEMPERATURE: 22.0})
        # Must not send anything — scale=0 is invalid, not a crash
        e.coordinator.async_send_dps.assert_not_awaited()


# ── async_set_hvac_mode when dp_power is None ─────────────────────────────────


class TestSetHvacModeNoDpPower:
    @pytest.mark.asyncio
    async def test_set_hvac_mode_noop_when_dp_power_none(self) -> None:
        """async_set_hvac_mode returns immediately if spec.dp_power is None."""
        from tuya_cloudless.profiles import EntitySpec

        coord = _make_coordinator(dps={})
        spec = EntitySpec(platform="climate", name="nopow")
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_nopow"
        entity._attr_translation_key = "nopow"
        entity._attr_hvac_modes = [HVACMode.OFF]
        entity._tuya_options = []
        entity._attr_min_temp = 7.0
        entity._attr_max_temp = 35.0
        entity._attr_target_temperature_step = 1.0
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_hvac_mode(HVACMode.HEAT)
        entity.coordinator.async_send_dps.assert_not_awaited()


# ── _get_tuya_mode fallback to static map ─────────────────────────────────────


class TestGetTuyaMode:
    def test_get_tuya_mode_uses_static_map_when_not_in_options(self) -> None:
        """_get_tuya_mode falls back to _HA_TO_TUYA_MODE when mode not in profile options."""
        # Use options that don't include the DRY mode so the loop finds nothing
        e = _make_climate({"1": True}, options=("heat", "cool"))
        # DRY is not in profile options, so fallback to _HA_TO_TUYA_MODE
        result = e._get_tuya_mode(HVACMode.DRY)
        assert result == "dry"

    def test_get_tuya_mode_returns_none_for_unknown_mode(self) -> None:
        """_get_tuya_mode returns None when neither profile options nor static map match."""
        from unittest.mock import patch

        from custom_components.tuya_cloudless.climate import _HA_TO_TUYA_MODE

        e = _make_climate({"1": True}, options=("heat",))
        # Temporarily remove FAN_ONLY from the static map for this test
        original = dict(_HA_TO_TUYA_MODE)
        modified = {k: v for k, v in original.items() if k != HVACMode.FAN_ONLY}
        with patch("custom_components.tuya_cloudless.climate._HA_TO_TUYA_MODE", modified):
            result = e._get_tuya_mode(HVACMode.FAN_ONLY)
        assert result is None


# ── async_turn_on / async_turn_off error revert ───────────────────────────────


class TestTurnOnOffErrorRevert:
    @pytest.mark.asyncio
    async def test_turn_on_reverts_on_error(self) -> None:
        """async_turn_on reverts optimistic state on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_climate({"1": False})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("fail")
        with pytest.raises(HomeAssistantError):
            await e.async_turn_on()
        assert e._optimistic_hvac_mode is None
        assert e.async_write_ha_state.call_count == 2

    @pytest.mark.asyncio
    async def test_turn_off_reverts_on_error(self) -> None:
        """async_turn_off reverts optimistic state on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_climate({"1": True, "2": "heat"})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("fail")
        with pytest.raises(HomeAssistantError):
            await e.async_turn_off()
        assert e._optimistic_hvac_mode is None
        assert e.async_write_ha_state.call_count == 2

    @pytest.mark.asyncio
    async def test_turn_on_noop_when_dp_power_none(self) -> None:
        """async_turn_on returns immediately when dp_power is None."""
        from tuya_cloudless.profiles import EntitySpec

        coord = _make_coordinator(dps={})
        spec = EntitySpec(platform="climate", name="nopow")
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_nopow"
        entity._attr_translation_key = "nopow"
        entity._attr_hvac_modes = [HVACMode.OFF]
        entity._tuya_options = []
        entity._attr_min_temp = 7.0
        entity._attr_max_temp = 35.0
        entity._attr_target_temperature_step = 1.0
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_on()
        entity.coordinator.async_send_dps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_noop_when_dp_power_none(self) -> None:
        """async_turn_off returns immediately when dp_power is None."""
        from tuya_cloudless.profiles import EntitySpec

        coord = _make_coordinator(dps={})
        spec = EntitySpec(platform="climate", name="nopow")
        entity = TuyaCloudlessClimate.__new__(TuyaCloudlessClimate)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        entity._attr_unique_id = "gw001_climate_nopow"
        entity._attr_translation_key = "nopow"
        entity._attr_hvac_modes = [HVACMode.OFF]
        entity._tuya_options = []
        entity._attr_min_temp = 7.0
        entity._attr_max_temp = 35.0
        entity._attr_target_temperature_step = 1.0
        entity._attr_supported_features = (
            ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        )
        entity._optimistic_hvac_mode = None
        entity._optimistic_target_temp = None
        entity._restored_state = None
        entity.async_write_ha_state = MagicMock()

        await entity.async_turn_off()
        entity.coordinator.async_send_dps.assert_not_awaited()


# ── R42-F2: Non-numeric DP guard ────────────────────────────────────────────


class TestClimateNonNumericDPR42:
    """R42-F2: current/target temperature must return None for non-numeric DPs."""

    def test_current_temperature_non_numeric_returns_none(self) -> None:
        """A string DP value in current_temperature returns None, not a crash."""
        e = _make_climate({"4": "error"})
        e._spec = _make_climate_spec(scale=0.1)
        assert e.current_temperature is None

    def test_target_temperature_non_numeric_returns_none(self) -> None:
        """A string DP value in target_temperature returns None, not a crash."""
        e = _make_climate({"3": "n/a"})
        e._spec = _make_climate_spec(scale=0.1)
        assert e.target_temperature is None

    def test_current_temperature_numeric_string_is_accepted(self) -> None:
        """A numeric string DP value is correctly converted to float."""
        e = _make_climate({"4": "215"})
        e._spec = _make_climate_spec(scale=0.1)
        # 215 * 0.1 = 21.5
        assert e.current_temperature == pytest.approx(21.5)

    def test_target_temperature_numeric_string_is_accepted(self) -> None:
        """A numeric string DP value for target temperature is correctly converted."""
        e = _make_climate({"3": "200"})
        e._spec = _make_climate_spec(scale=0.1)
        # 200 * 0.1 = 20.0
        assert e.target_temperature == pytest.approx(20.0)


class TestClimateTemperatureOverflowR49:
    """R49-F6: async_set_temperature must guard against inf/nan temperatures."""

    @pytest.mark.asyncio
    async def test_inf_temperature_is_rejected(self) -> None:
        """float('inf') temperature must log and return without crashing."""
        import math

        e = _make_climate({"3": 220})
        e._spec = _make_climate_spec(scale=0.1)
        e.coordinator.async_send_dps = AsyncMock()
        await e.async_set_temperature(**{ATTR_TEMPERATURE: float("inf")})
        e.coordinator.async_send_dps.assert_not_called()
        # optimistic state should NOT be set
        assert e._optimistic_target_temp is None or not math.isinf(e._optimistic_target_temp or 0.0)

    @pytest.mark.asyncio
    async def test_nan_temperature_is_rejected(self) -> None:
        """float('nan') temperature must log and return without crashing."""
        e = _make_climate({"3": 220})
        e._spec = _make_climate_spec(scale=0.1)
        e.coordinator.async_send_dps = AsyncMock()
        await e.async_set_temperature(**{ATTR_TEMPERATURE: float("nan")})
        e.coordinator.async_send_dps.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_temperature_is_sent(self) -> None:
        """A normal float temperature is sent normally."""
        e = _make_climate({"3": 220})
        e._spec = _make_climate_spec(scale=0.1)
        e.coordinator.async_send_dps = AsyncMock()
        await e.async_set_temperature(**{ATTR_TEMPERATURE: 22.5})
        e.coordinator.async_send_dps.assert_called_once()
