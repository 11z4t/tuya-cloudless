"""Tests for custom_components.tuya_cloudless.button."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.button import TuyaCloudlessButton

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


def _make_button_dp_power(dp_id: str = "1", name: str = "reset") -> EntitySpec:
    return EntitySpec(
        platform="button",
        name=name,
        dp_power=DPSpec(id=dp_id, type="bool"),
    )


def _make_button_dp_value(dp_id: str = "7", name: str = "defrost") -> EntitySpec:
    return EntitySpec(
        platform="button",
        name=name,
        dp_value=DPSpec(id=dp_id, type="raw"),
    )


def _make_button(
    spec: EntitySpec | None = None,
    gw_id: str = "gw001",
) -> TuyaCloudlessButton:
    if spec is None:
        spec = _make_button_dp_power()
    coord = _make_coordinator(gw_id=gw_id)
    entity = TuyaCloudlessButton.__new__(TuyaCloudlessButton)
    entity.coordinator = coord
    dp_id = (
        spec.dp_power.id
        if spec.dp_power is not None
        else (spec.dp_value.id if spec.dp_value is not None else None)
    )
    entity._dp_id = dp_id
    entity._spec = spec
    entity._attr_unique_id = f"{gw_id}_{spec.platform}_{spec.name}"
    entity._attr_translation_key = spec.name
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestTuyaCloudlessButton:
    def test_unique_id_format_dp_power(self) -> None:
        e = _make_button(_make_button_dp_power(name="reset"))
        assert e._attr_unique_id == "gw001_button_reset"

    def test_unique_id_format_dp_value(self) -> None:
        e = _make_button(_make_button_dp_value(name="defrost"))
        assert e._attr_unique_id == "gw001_button_defrost"

    def test_dp_id_uses_dp_power(self) -> None:
        e = _make_button(_make_button_dp_power(dp_id="3"))
        assert e._dp_id == "3"

    def test_dp_id_falls_back_to_dp_value(self) -> None:
        e = _make_button(_make_button_dp_value(dp_id="7"))
        assert e._dp_id == "7"

    def test_dp_id_none_when_no_dp(self) -> None:
        spec = EntitySpec(platform="button", name="noop")
        e = _make_button(spec)
        assert e._dp_id is None

    @pytest.mark.asyncio
    async def test_press_sends_true_via_dp_power(self) -> None:
        e = _make_button(_make_button_dp_power(dp_id="1"))
        await e.async_press()
        e.coordinator.async_send_dps.assert_awaited_once_with({"1": True})

    @pytest.mark.asyncio
    async def test_press_sends_true_via_dp_value_fallback(self) -> None:
        e = _make_button(_make_button_dp_value(dp_id="7"))
        await e.async_press()
        e.coordinator.async_send_dps.assert_awaited_once_with({"7": True})

    @pytest.mark.asyncio
    async def test_press_prefers_dp_power_over_dp_value(self) -> None:
        spec = EntitySpec(
            platform="button",
            name="dual",
            dp_power=DPSpec(id="1", type="bool"),
            dp_value=DPSpec(id="7", type="raw"),
        )
        e = _make_button(spec)
        await e.async_press()
        # Only dp_power should be used
        e.coordinator.async_send_dps.assert_awaited_once_with({"1": True})

    @pytest.mark.asyncio
    async def test_press_noop_when_no_dp_configured(self) -> None:
        spec = EntitySpec(platform="button", name="noop")
        e = _make_button(spec)
        await e.async_press()
        e.coordinator.async_send_dps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_press_different_dp_ids(self) -> None:
        e = _make_button(_make_button_dp_power(dp_id="12"))
        await e.async_press()
        e.coordinator.async_send_dps.assert_awaited_once_with({"12": True})

    def test_translation_key(self) -> None:
        e = _make_button(_make_button_dp_power(name="start_cycle"))
        assert e._attr_translation_key == "start_cycle"


class TestButtonSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.button import async_setup_entry

        coord = _make_coordinator()
        spec = _make_button_dp_power()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Test Device",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list[Any] = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessButton)

    @pytest.mark.asyncio
    async def test_setup_skips_non_button(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.button import async_setup_entry

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

    @pytest.mark.asyncio
    async def test_setup_creates_multiple_buttons(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.button import async_setup_entry

        coord = _make_coordinator()
        spec1 = _make_button_dp_power(dp_id="1", name="reset")
        spec2 = _make_button_dp_value(dp_id="7", name="defrost")
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec1, spec2],
            profile_name="Multi Button Device",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list[Any] = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        assert len(added) == 2
        assert all(isinstance(e, TuyaCloudlessButton) for e in added)
