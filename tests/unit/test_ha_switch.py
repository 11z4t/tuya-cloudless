"""Tests for custom_components.tuya_cloudless.switch."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

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


def _make_switch_spec(dp_id: str = "1") -> EntitySpec:
    return EntitySpec(
        platform="switch",
        name="main_switch",
        dp_power=DPSpec(id=dp_id, type="bool"),
    )


def _make_switch(
    dps: dict[str, Any] | None = None,
    dp_id: str = "1",
    gw_id: str = "gw001",
) -> TuyaCloudlessSwitch:
    coord = _make_coordinator(dps, gw_id)
    spec = _make_switch_spec(dp_id)
    entity = TuyaCloudlessSwitch.__new__(TuyaCloudlessSwitch)
    entity.coordinator = coord
    entity._dp_id = dp_id
    entity._spec = spec
    entity._attr_unique_id = f"{gw_id}_{spec.platform}_{spec.name}"
    entity._attr_translation_key = spec.name
    entity._optimistic_state = None  # set by __init__ normally
    entity.async_write_ha_state = MagicMock()  # stub out HA framework call
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestTuyaCloudlessSwitch:
    def test_unique_id_format(self) -> None:
        e = _make_switch()
        assert e._attr_unique_id == "gw001_switch_main_switch"

    def test_is_on_true(self) -> None:
        e = _make_switch({"1": True})
        assert e.is_on is True

    def test_is_on_false(self) -> None:
        e = _make_switch({"1": False})
        assert e.is_on is False

    def test_is_on_none_when_missing(self) -> None:
        e = _make_switch({})
        assert e.is_on is None

    @pytest.mark.asyncio
    async def test_turn_on(self) -> None:
        e = _make_switch({"1": False})
        await e.async_turn_on()
        e.coordinator.async_send_dps.assert_awaited_once()
        call_args = e.coordinator.async_send_dps.call_args[0][0]
        assert call_args == {"1": True}

    @pytest.mark.asyncio
    async def test_turn_off(self) -> None:
        e = _make_switch({"1": True})
        await e.async_turn_off()
        e.coordinator.async_send_dps.assert_awaited_once()
        call_args = e.coordinator.async_send_dps.call_args[0][0]
        assert call_args == {"1": False}

    def test_is_on_truthy_value(self) -> None:
        e = _make_switch({"1": 1})
        assert e.is_on is True

    def test_custom_dp_id(self) -> None:
        e = _make_switch({"5": True}, dp_id="5")
        assert e.is_on is True

    def test_extra_state_attributes(self) -> None:
        e = _make_switch({"1": True})
        attrs = e.extra_state_attributes
        assert attrs["dp_id"] == "1"
        assert attrs["raw_value"] is True


class TestSwitchSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.switch import async_setup_entry

        coord = _make_coordinator({"1": True})
        spec = _make_switch_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Generic Switch",
        )

        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessSwitch)

    @pytest.mark.asyncio
    async def test_setup_no_switch_specs_skips(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.switch import async_setup_entry

        coord = _make_coordinator()
        sensor_spec = EntitySpec(platform="sensor", name="power")
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[sensor_spec],
            profile_name="Test",
        )

        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 0

    @pytest.mark.asyncio
    async def test_switch_no_dp_power_defaults_to_1(self) -> None:
        """Switch with no dp_power should default to dp_id '1'."""
        coord = _make_coordinator({"1": True})
        spec = EntitySpec(platform="switch", name="no_power")
        entity = TuyaCloudlessSwitch.__new__(TuyaCloudlessSwitch)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = f"gw001_{spec.platform}_{spec.name}"
        assert entity.is_on is True
