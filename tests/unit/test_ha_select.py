"""Tests for custom_components.tuya_cloudless.select."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.select import TuyaCloudlessSelect

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


def _make_select_spec(
    dp_id: str = "5",
    options: tuple[str, ...] = ("sound1", "sound2", "alarm"),
) -> EntitySpec:
    return EntitySpec(
        platform="select",
        name="siren_mode",
        dp_value=DPSpec(id=dp_id, type="enum"),
        dp_options=options,
    )


def _make_select(
    dps: dict[str, Any] | None = None,
    dp_id: str = "5",
    options: tuple[str, ...] = ("sound1", "sound2", "alarm"),
    gw_id: str = "gw001",
) -> TuyaCloudlessSelect:
    coord = _make_coordinator(dps, gw_id)
    spec = _make_select_spec(dp_id, options)
    entity = TuyaCloudlessSelect.__new__(TuyaCloudlessSelect)
    entity.coordinator = coord
    entity._dp_id = dp_id
    entity._spec = spec
    entity._attr_unique_id = f"{gw_id}_{spec.platform}_{spec.name}"
    entity._attr_translation_key = spec.name
    entity._attr_options = list(options)
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestTuyaCloudlessSelect:
    def test_unique_id_format(self) -> None:
        e = _make_select()
        assert e._attr_unique_id == "gw001_select_siren_mode"

    def test_options_list(self) -> None:
        e = _make_select(options=("off", "heat", "cool"))
        assert e._attr_options == ["off", "heat", "cool"]

    def test_current_option_valid(self) -> None:
        e = _make_select({"5": "sound2"})
        assert e.current_option == "sound2"

    def test_current_option_none_when_missing(self) -> None:
        e = _make_select({})
        assert e.current_option is None

    def test_current_option_none_when_invalid(self) -> None:
        e = _make_select({"5": "unknown_value"})
        assert e.current_option is None

    def test_current_option_first_valid(self) -> None:
        e = _make_select({"5": "alarm"})
        assert e.current_option == "alarm"

    @pytest.mark.asyncio
    async def test_select_option(self) -> None:
        e = _make_select({"5": "sound1"})
        await e.async_select_option("alarm")
        e.coordinator.async_send_dps.assert_awaited_once_with({"5": "alarm"})

    @pytest.mark.asyncio
    async def test_select_option_heat(self) -> None:
        e = _make_select(options=("off", "heat", "cool"))
        await e.async_select_option("heat")
        e.coordinator.async_send_dps.assert_awaited_once_with({"5": "heat"})

    @pytest.mark.asyncio
    async def test_select_option_no_dp_spec(self) -> None:
        coord = _make_coordinator({})
        spec = EntitySpec(platform="select", name="siren_mode")
        entity = TuyaCloudlessSelect.__new__(TuyaCloudlessSelect)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        entity._attr_options = ["a", "b"]
        await entity.async_select_option("a")
        coord.async_send_dps.assert_not_awaited()

    def test_current_option_none_when_no_dp_spec(self) -> None:
        coord = _make_coordinator({})
        spec = EntitySpec(platform="select", name="siren_mode")
        entity = TuyaCloudlessSelect.__new__(TuyaCloudlessSelect)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        entity._attr_options = ["a", "b"]
        assert entity.current_option is None


class TestSelectSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.select import async_setup_entry

        coord = _make_coordinator({"5": "sound1"})
        spec = _make_select_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            device_info=MagicMock(),
            entity_specs=[spec],
            profile_name="Siren / Alarm",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list[Any] = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessSelect)

    @pytest.mark.asyncio
    async def test_setup_skips_non_select(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.select import async_setup_entry

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
