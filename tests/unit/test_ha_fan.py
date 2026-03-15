"""Tests for custom_components.tuya_cloudless.fan."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.fan import FanEntityFeature
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.fan import TuyaCloudlessFan

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


def _make_full_fan_spec() -> EntitySpec:
    """Fan with all DPs: power, speed, direction, preset modes, oscillation."""
    return EntitySpec(
        platform="fan",
        name="main_fan",
        dp_power=DPSpec(id="1", type="bool"),
        dp_value=DPSpec(id="3", type="int", min_raw=1, max_raw=6),
        dp_direction=DPSpec(id="4", type="enum"),
        dp_mode=DPSpec(id="2", type="enum"),
        dp_oscillate=DPSpec(id="8", type="bool"),
        dp_options=("sleep", "auto", "natural"),
    )


def _make_power_only_fan_spec() -> EntitySpec:
    """Minimal fan with only power DP."""
    return EntitySpec(
        platform="fan",
        name="main_fan",
        dp_power=DPSpec(id="1", type="bool"),
    )


def _make_fan(
    dps: dict[str, Any] | None = None,
    spec: EntitySpec | None = None,
    gw_id: str = "gw001",
) -> TuyaCloudlessFan:
    coord = _make_coordinator(dps, gw_id)
    if spec is None:
        spec = _make_full_fan_spec()
    entity = TuyaCloudlessFan.__new__(TuyaCloudlessFan)
    entity.coordinator = coord
    entity._dp_id = spec.dp_power.id if spec.dp_power else "1"
    entity._spec = spec
    entity._attr_unique_id = f"{gw_id}_{spec.platform}_{spec.name}"
    entity._attr_translation_key = spec.name
    entity._attr_preset_modes = (
        list(spec.dp_options) if spec.dp_mode is not None and spec.dp_options else None
    )
    # Compute supported features same as __init__
    features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    if spec.dp_value is not None:
        features |= FanEntityFeature.SET_SPEED
    if spec.dp_mode is not None and spec.dp_options:
        features |= FanEntityFeature.PRESET_MODE
    if spec.dp_oscillate is not None:
        features |= FanEntityFeature.OSCILLATE
    if spec.dp_direction is not None:
        features |= FanEntityFeature.DIRECTION
    entity._attr_supported_features = features
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestTuyaCloudlessFan:
    def test_unique_id_format(self) -> None:
        e = _make_fan()
        assert e._attr_unique_id == "gw001_fan_main_fan"

    def test_is_on_true(self) -> None:
        e = _make_fan({"1": True})
        assert e.is_on is True

    def test_is_on_false(self) -> None:
        e = _make_fan({"1": False})
        assert e.is_on is False

    def test_is_on_none_when_missing(self) -> None:
        e = _make_fan({})
        assert e.is_on is None

    def test_percentage_when_present(self) -> None:
        # dp_value: min_raw=1, max_raw=6 → raw=4 maps to (4-1)/(6-1)*100 = 60%
        e = _make_fan({"3": 4})
        assert e.percentage == 60

    def test_percentage_min_raw_maps_to_zero(self) -> None:
        # raw=1 (min_raw) → 0%
        e = _make_fan({"3": 1})
        assert e.percentage == 0

    def test_percentage_max_raw_maps_to_100(self) -> None:
        # raw=6 (max_raw) → 100%
        e = _make_fan({"3": 6})
        assert e.percentage == 100

    def test_percentage_none_when_missing(self) -> None:
        e = _make_fan({})
        assert e.percentage is None

    def test_percentage_none_when_no_dp_value(self) -> None:
        e = _make_fan({"1": True}, spec=_make_power_only_fan_spec())
        assert e.percentage is None

    def test_preset_mode(self) -> None:
        e = _make_fan({"2": "auto"})
        assert e.preset_mode == "auto"

    def test_preset_mode_none_when_missing(self) -> None:
        e = _make_fan({})
        assert e.preset_mode is None

    def test_oscillating(self) -> None:
        e = _make_fan({"8": True})
        assert e.oscillating is True

    def test_oscillating_false(self) -> None:
        e = _make_fan({"8": False})
        assert e.oscillating is False

    def test_current_direction(self) -> None:
        e = _make_fan({"4": "forward"})
        assert e.current_direction == "forward"

    def test_supported_features_full(self) -> None:
        e = _make_fan()
        assert e._attr_supported_features & FanEntityFeature.SET_SPEED
        assert e._attr_supported_features & FanEntityFeature.PRESET_MODE
        assert e._attr_supported_features & FanEntityFeature.OSCILLATE
        assert e._attr_supported_features & FanEntityFeature.DIRECTION
        assert e._attr_supported_features & FanEntityFeature.TURN_ON
        assert e._attr_supported_features & FanEntityFeature.TURN_OFF

    def test_supported_features_power_only(self) -> None:
        e = _make_fan(spec=_make_power_only_fan_spec())
        assert not (e._attr_supported_features & FanEntityFeature.SET_SPEED)
        assert not (e._attr_supported_features & FanEntityFeature.PRESET_MODE)
        assert not (e._attr_supported_features & FanEntityFeature.OSCILLATE)
        assert not (e._attr_supported_features & FanEntityFeature.DIRECTION)
        assert e._attr_supported_features & FanEntityFeature.TURN_ON
        assert e._attr_supported_features & FanEntityFeature.TURN_OFF

    @pytest.mark.asyncio
    async def test_turn_on(self) -> None:
        e = _make_fan({"1": False})
        await e.async_turn_on()
        call_args = e.coordinator.async_send_dps.call_args[0][0]
        assert call_args["1"] is True

    @pytest.mark.asyncio
    async def test_turn_off(self) -> None:
        e = _make_fan({"1": True})
        await e.async_turn_off()
        e.coordinator.async_send_dps.assert_awaited_once_with({"1": False})

    @pytest.mark.asyncio
    async def test_set_percentage(self) -> None:
        # 50% of min_raw=1, max_raw=6 → 1 + round(5*50/100) = 1 + 3 (round(2.5)=2 in Python) = 3
        e = _make_fan({"1": True})
        await e.async_set_percentage(60)
        # 60% → 1 + round(5 * 60 / 100) = 1 + round(3.0) = 4
        e.coordinator.async_send_dps.assert_awaited_once_with({"3": 4})

    @pytest.mark.asyncio
    async def test_oscillate_true(self) -> None:
        e = _make_fan({"1": True})
        await e.async_oscillate(True)
        e.coordinator.async_send_dps.assert_awaited_once_with({"8": True})

    @pytest.mark.asyncio
    async def test_set_preset_mode(self) -> None:
        e = _make_fan({"1": True})
        await e.async_set_preset_mode("sleep")
        e.coordinator.async_send_dps.assert_awaited_once_with({"2": "sleep"})

    @pytest.mark.asyncio
    async def test_set_direction(self) -> None:
        e = _make_fan({"1": True})
        await e.async_set_direction("reverse")
        e.coordinator.async_send_dps.assert_awaited_once_with({"4": "reverse"})


class TestFanSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.fan import async_setup_entry

        coord = _make_coordinator({"1": True})
        spec = _make_full_fan_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            device_info=MagicMock(),
            entity_specs=[spec],
            profile_name="Ceiling Fan",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list[Any] = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessFan)

    @pytest.mark.asyncio
    async def test_setup_skips_non_fan(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.fan import async_setup_entry

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


class TestFanExtraStateAttributes:
    def test_all_dps_present(self) -> None:
        fan = _make_fan(dps={"1": True, "2": "sleep", "3": 4, "4": "forward", "8": False})
        attrs = fan.extra_state_attributes
        assert attrs["dp_power_raw"] is True
        assert attrs["dp_speed_raw"] == 4
        assert attrs["dp_mode_raw"] == "sleep"
        assert attrs["dp_direction_raw"] == "forward"
        assert attrs["dp_oscillate_raw"] is False

    def test_missing_dps_return_none(self) -> None:
        fan = _make_fan(dps={})
        attrs = fan.extra_state_attributes
        assert attrs["dp_power_raw"] is None
        assert attrs["dp_speed_raw"] is None

    def test_power_only_fan_omits_optional_keys(self) -> None:
        coord = _make_coordinator(dps={"1": True})
        spec = _make_power_only_fan_spec()
        from custom_components.tuya_cloudless.fan import TuyaCloudlessFan

        fan = TuyaCloudlessFan(coord, spec)
        attrs = fan.extra_state_attributes
        assert "dp_power_raw" in attrs
        assert "dp_speed_raw" not in attrs
        assert "dp_mode_raw" not in attrs
        assert "dp_oscillate_raw" not in attrs
        assert "dp_direction_raw" not in attrs


class TestFanInit:
    """Test TuyaCloudlessFan.__init__ attribute assignments via real constructor."""

    def _make(self, spec: EntitySpec | None = None, gw_id: str = "mydev") -> TuyaCloudlessFan:
        from custom_components.tuya_cloudless.fan import TuyaCloudlessFan

        coord = _make_coordinator(gw_id=gw_id)
        return TuyaCloudlessFan(coord, spec or _make_full_fan_spec())

    def test_unique_id_format(self) -> None:
        entity = self._make(gw_id="mydev")
        assert entity._attr_unique_id == "mydev_fan_main_fan"

    def test_preset_modes_from_options(self) -> None:
        entity = self._make()
        assert entity.preset_modes == ["sleep", "auto", "natural"]

    def test_preset_modes_none_when_no_dp_mode(self) -> None:
        spec = _make_power_only_fan_spec()
        entity = self._make(spec=spec)
        assert entity.preset_modes is None

    def test_supported_features_full_spec(self) -> None:
        entity = self._make()
        assert FanEntityFeature.SET_SPEED in entity.supported_features
        assert FanEntityFeature.PRESET_MODE in entity.supported_features
        assert FanEntityFeature.OSCILLATE in entity.supported_features
        assert FanEntityFeature.DIRECTION in entity.supported_features
        assert FanEntityFeature.TURN_ON in entity.supported_features

    def test_supported_features_power_only(self) -> None:
        spec = _make_power_only_fan_spec()
        entity = self._make(spec=spec)
        assert FanEntityFeature.SET_SPEED not in entity.supported_features
        assert FanEntityFeature.PRESET_MODE not in entity.supported_features
        assert FanEntityFeature.TURN_ON in entity.supported_features

    def test_translation_key(self) -> None:
        entity = self._make()
        assert entity._attr_translation_key == "main_fan"
