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
