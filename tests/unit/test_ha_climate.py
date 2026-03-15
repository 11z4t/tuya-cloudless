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
            device_info=MagicMock(),
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
            device_info=MagicMock(),
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
