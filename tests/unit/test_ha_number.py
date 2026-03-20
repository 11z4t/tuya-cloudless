"""Tests for custom_components.tuya_cloudless.number."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.number import TuyaCloudlessNumber

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


def _make_number_spec(
    dp_id: str = "6",
    min_raw: int = 0,
    max_raw: int = 100,
    scale: float = 1.0,
    target_min: float | None = None,
    target_max: float | None = None,
    step: float = 1.0,
    unit: str | None = None,
) -> EntitySpec:
    return EntitySpec(
        platform="number",
        name="volume",
        dp_value=DPSpec(id=dp_id, type="int", min_raw=min_raw, max_raw=max_raw, scale=scale),
        target_min=target_min,
        target_max=target_max,
        step=step,
        unit=unit,
    )


def _make_number(
    dps: dict[str, Any] | None = None,
    dp_id: str = "6",
    min_raw: int = 0,
    max_raw: int = 100,
    scale: float = 1.0,
    target_min: float | None = None,
    target_max: float | None = None,
    step: float = 1.0,
    gw_id: str = "gw001",
) -> TuyaCloudlessNumber:
    coord = _make_coordinator(dps, gw_id)
    spec = _make_number_spec(dp_id, min_raw, max_raw, scale, target_min, target_max, step)
    entity = TuyaCloudlessNumber.__new__(TuyaCloudlessNumber)
    entity.coordinator = coord
    entity._dp_id = dp_id
    entity._spec = spec
    entity._attr_unique_id = f"{gw_id}_{spec.platform}_{spec.name}"
    entity._attr_translation_key = spec.name
    # Set computed attrs (mirrors __init__ logic)
    entity._attr_native_min_value = target_min if target_min is not None else float(min_raw)
    entity._attr_native_max_value = target_max if target_max is not None else float(max_raw)
    entity._attr_native_step = step
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestTuyaCloudlessNumber:
    def test_unique_id_format(self) -> None:
        e = _make_number()
        assert e._attr_unique_id == "gw001_number_volume"

    def test_native_value_raw(self) -> None:
        e = _make_number({"6": 50})
        assert e.native_value == pytest.approx(50.0)

    def test_native_value_with_scale(self) -> None:
        e = _make_number({"6": 250}, scale=0.1)
        assert e.native_value == pytest.approx(25.0)

    def test_native_value_none_when_missing(self) -> None:
        e = _make_number({})
        assert e.native_value is None

    def test_native_value_none_when_no_dp_value(self) -> None:
        coord = _make_coordinator({})
        spec = EntitySpec(platform="number", name="volume")
        entity = TuyaCloudlessNumber.__new__(TuyaCloudlessNumber)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        assert entity.native_value is None

    def test_native_min_value_from_target_min(self) -> None:
        e = _make_number(target_min=5.0)
        assert e._attr_native_min_value == pytest.approx(5.0)

    def test_native_max_value_from_target_max(self) -> None:
        e = _make_number(target_max=80.0)
        assert e._attr_native_max_value == pytest.approx(80.0)

    def test_native_min_max_from_dp_raw(self) -> None:
        e = _make_number(min_raw=10, max_raw=90)
        assert e._attr_native_min_value == pytest.approx(10.0)
        assert e._attr_native_max_value == pytest.approx(90.0)

    def test_native_step(self) -> None:
        e = _make_number(step=0.5)
        assert e._attr_native_step == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_set_native_value_scale_1(self) -> None:
        e = _make_number({"6": 30})
        await e.async_set_native_value(50.0)
        e.coordinator.async_send_dps.assert_awaited_once_with({"6": 50})

    @pytest.mark.asyncio
    async def test_set_native_value_with_scale(self) -> None:
        e = _make_number({"6": 0}, scale=0.1)
        await e.async_set_native_value(25.0)
        e.coordinator.async_send_dps.assert_awaited_once_with({"6": 250})

    @pytest.mark.asyncio
    async def test_set_native_value_no_dp_spec(self) -> None:
        coord = _make_coordinator({})
        spec = EntitySpec(platform="number", name="volume")
        entity = TuyaCloudlessNumber.__new__(TuyaCloudlessNumber)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        await entity.async_set_native_value(50.0)
        coord.async_send_dps.assert_not_awaited()


class TestNumberDeviceClassAndUnit:
    """Tests for unit (line 83) and device_class (lines 86-91) in __init__."""

    def test_unit_sets_native_unit_of_measurement(self) -> None:
        """spec.unit sets _attr_native_unit_of_measurement (line 83)."""
        coord = _make_coordinator()
        spec = _make_number_spec(unit="°C")
        entity = TuyaCloudlessNumber(coord, spec)
        assert entity._attr_native_unit_of_measurement == "°C"

    def test_no_unit_does_not_set_attr(self) -> None:
        """When spec.unit is None, _attr_native_unit_of_measurement is not set."""
        coord = _make_coordinator()
        spec = _make_number_spec()  # unit=None by default
        entity = TuyaCloudlessNumber(coord, spec)
        assert (
            not hasattr(entity, "_attr_native_unit_of_measurement")
            or entity._attr_native_unit_of_measurement is None
        )

    def test_valid_device_class_sets_attr(self) -> None:
        """Valid device_class string sets _attr_device_class (lines 86-91)."""
        from homeassistant.components.number import NumberDeviceClass

        coord = _make_coordinator()
        spec = EntitySpec(
            platform="number",
            name="temperature",
            dp_value=DPSpec(id="6", type="int", min_raw=0, max_raw=100),
            device_class="temperature",
        )
        entity = TuyaCloudlessNumber(coord, spec)
        assert entity._attr_device_class == NumberDeviceClass.TEMPERATURE

    def test_invalid_device_class_logs_warning_and_is_ignored(self) -> None:
        """Invalid device_class logs warning and does not crash or set attr."""
        coord = _make_coordinator()
        spec = EntitySpec(
            platform="number",
            name="mystery",
            dp_value=DPSpec(id="6", type="int", min_raw=0, max_raw=100),
            device_class="invalid_device_class",
        )
        entity = TuyaCloudlessNumber(coord, spec)
        assert getattr(entity, "_attr_device_class", None) != "invalid_device_class"


class TestNumberSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.number import async_setup_entry

        coord = _make_coordinator({"6": 50})
        spec = _make_number_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Siren / Alarm",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list[Any] = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessNumber)

    @pytest.mark.asyncio
    async def test_setup_skips_non_number(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.number import async_setup_entry

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


# ── R42-F3: Scale applied to min/max ────────────────────────────────────────


class TestNumberScaleBoundsR42:
    """R42-F3: native_min/max_value must apply scale factor from DP spec."""

    def test_min_max_scaled_via_init(self) -> None:
        """When built via __init__, min/max use scale (e.g. raw 0-1000, scale=0.1 → 0.0-100.0)."""
        coord = _make_coordinator({})
        spec = _make_number_spec(min_raw=0, max_raw=1000, scale=0.1)
        entity = TuyaCloudlessNumber(coord, spec)
        assert entity._attr_native_min_value == pytest.approx(0.0)
        assert entity._attr_native_max_value == pytest.approx(100.0)

    def test_min_max_scale_applied_correctly(self) -> None:
        """min_raw=100, max_raw=500, scale=0.5 → display 50.0-250.0."""
        coord = _make_coordinator({})
        spec = _make_number_spec(min_raw=100, max_raw=500, scale=0.5)
        entity = TuyaCloudlessNumber(coord, spec)
        assert entity._attr_native_min_value == pytest.approx(50.0)
        assert entity._attr_native_max_value == pytest.approx(250.0)

    def test_target_min_max_override_raw_scale(self) -> None:
        """Explicit target_min/target_max override scale computation."""
        coord = _make_coordinator({})
        spec = _make_number_spec(
            min_raw=0, max_raw=1000, scale=0.1, target_min=5.0, target_max=80.0
        )
        entity = TuyaCloudlessNumber(coord, spec)
        assert entity._attr_native_min_value == pytest.approx(5.0)
        assert entity._attr_native_max_value == pytest.approx(80.0)

    def test_native_value_clamped_to_scaled_bounds(self) -> None:
        """native_value is clamped to [native_min_value, native_max_value]."""
        coord = _make_coordinator({"6": 1500})  # exceeds max_raw=1000
        spec = _make_number_spec(min_raw=0, max_raw=1000, scale=0.1)
        entity = TuyaCloudlessNumber(coord, spec)
        # 1500 * 0.1 = 150.0, but max is 100.0
        assert entity.native_value == pytest.approx(100.0)


class TestNumberValueOverflowR49:
    """R49-F7: async_set_native_value must guard against overflow from tiny scale."""

    @pytest.mark.asyncio
    async def test_tiny_scale_overflow_is_ignored(self) -> None:
        """Scale near zero causing inf result must log and not send DP."""
        from tuya_cloudless.profiles import DPSpec, EntitySpec

        from custom_components.tuya_cloudless.coordinator import DeviceState
        from custom_components.tuya_cloudless.number import TuyaCloudlessNumber

        coord = MagicMock()
        coord.gw_id = "gw1"
        coord.device_name = "gw1"
        coord.profile_name = ""
        coord._version = "3.3"
        coord.version = "3.3"
        coord.state = DeviceState(available=True, dps={}, last_seen=None, reconnect_count=0)
        coord.async_send_dps = AsyncMock()

        spec = EntitySpec(
            platform="number",
            name="brightness",
            dp_value=DPSpec(id="1", type="int", scale=0.1, min_raw=0, max_raw=1000),
        )
        entity = TuyaCloudlessNumber.__new__(TuyaCloudlessNumber)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_native_min_value = 0.0
        entity._attr_native_max_value = 100.0

        # value=inf / scale=0.1 → inf, which is not finite → guard fires, DP not sent
        await entity.async_set_native_value(float("inf"))
        coord.async_send_dps.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_value_is_sent(self) -> None:
        """Normal value/scale combination is sent correctly."""
        from tuya_cloudless.profiles import DPSpec, EntitySpec

        from custom_components.tuya_cloudless.coordinator import DeviceState
        from custom_components.tuya_cloudless.number import TuyaCloudlessNumber

        coord = MagicMock()
        coord.gw_id = "gw1"
        coord.device_name = "gw1"
        coord.profile_name = ""
        coord._version = "3.3"
        coord.version = "3.3"
        coord.state = DeviceState(available=True, dps={}, last_seen=None, reconnect_count=0)
        coord.async_send_dps = AsyncMock()

        spec = EntitySpec(
            platform="number",
            name="brightness",
            dp_value=DPSpec(id="1", type="int", scale=0.1, min_raw=0, max_raw=1000),
        )
        entity = TuyaCloudlessNumber.__new__(TuyaCloudlessNumber)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_native_min_value = 0.0
        entity._attr_native_max_value = 100.0

        await entity.async_set_native_value(50.0)
        coord.async_send_dps.assert_called_once_with({"1": 500})


class TestNumberEdgeCases:
    """Cover remaining branches: ValueError in native_value, scale=0, HA error re-raise."""

    def test_native_value_non_numeric_dp_returns_none(self) -> None:
        """native_value returns None when DP is a non-numeric string (lines 117-118)."""
        e = _make_number({"6": "not_a_number"})
        assert e.native_value is None

    def test_native_value_none_type_dp_returns_none(self) -> None:
        """native_value returns None when DP value is a list (TypeError path)."""
        e = _make_number({"6": [1, 2, 3]})
        assert e.native_value is None

    @pytest.mark.asyncio
    async def test_set_native_value_scale_zero_logs_and_returns(self) -> None:
        """scale=0 logs warning and returns without calling async_send_dps (lines 132-133)."""
        e = _make_number({"6": 50}, scale=0.0)
        await e.async_set_native_value(50.0)
        e.coordinator.async_send_dps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_native_value_reraises_ha_error(self) -> None:
        """HomeAssistantError from coordinator is re-raised (lines 149-150)."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_number({"6": 50})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_set_native_value(50.0)
