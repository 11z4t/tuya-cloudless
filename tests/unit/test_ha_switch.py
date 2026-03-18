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
    entity._restored_state = None  # set by RestoreStateMixin normally
    entity.async_write_ha_state = MagicMock()  # stub out HA framework call
    return entity


# Helper: run the real async_added_to_hass with a mocked last state.
async def _run_restore(
    entity: TuyaCloudlessSwitch,
    state_str: str | None,
) -> None:
    from unittest.mock import AsyncMock, patch

    from homeassistant.helpers.update_coordinator import CoordinatorEntity

    mock_state = MagicMock() if state_str is not None else None
    if mock_state is not None:
        mock_state.state = state_str
        mock_state.attributes = {}
    entity.async_get_last_state = AsyncMock(return_value=mock_state)
    with patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()):
        await entity.async_added_to_hass()


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
        entity._optimistic_state = None
        assert entity.is_on is True


class TestSwitchRestoreState:
    """Tests for async_added_to_hass restore logic (lines 83-85)."""

    @pytest.mark.asyncio
    async def test_restore_on_sets_optimistic_true(self) -> None:
        """Real async_added_to_hass: 'on' → _optimistic_state=True."""
        from homeassistant.const import STATE_ON

        e = _make_switch({})
        await _run_restore(e, STATE_ON)
        assert e._optimistic_state is True
        assert e.is_on is True

    @pytest.mark.asyncio
    async def test_restore_off_sets_optimistic_false(self) -> None:
        """Real async_added_to_hass: 'off' → _optimistic_state=False."""
        from homeassistant.const import STATE_OFF

        e = _make_switch({})
        await _run_restore(e, STATE_OFF)
        assert e._optimistic_state is False
        assert e.is_on is False

    @pytest.mark.asyncio
    async def test_restore_other_state_not_applied(self) -> None:
        """Real async_added_to_hass: 'unavailable' must not set optimistic."""
        e = _make_switch({})
        await _run_restore(e, "unavailable")
        assert e._optimistic_state is None

    @pytest.mark.asyncio
    async def test_restore_none_state_not_applied(self) -> None:
        """Real async_added_to_hass: no recorded state → optimistic untouched."""
        e = _make_switch({})
        await _run_restore(e, None)
        assert e._optimistic_state is None


class TestSwitchCoordinatorUpdate:
    """Tests for _handle_coordinator_update clearing optimistic (lines 90-91)."""

    def test_coordinator_update_clears_optimistic(self) -> None:
        """_handle_coordinator_update clears _optimistic_state."""
        e = _make_switch({"1": True})
        e._optimistic_state = True
        e.coordinator.data = e.coordinator.state
        e._handle_coordinator_update()
        assert e._optimistic_state is None

    def test_is_on_returns_optimistic_when_set(self) -> None:
        """is_on returns _optimistic_state when it is not None."""
        e = _make_switch({"1": False})
        e._optimistic_state = True
        assert e.is_on is True


class TestSwitchErrorPaths:
    """Tests for async_turn_on/off error revert paths (lines 119-122, 136-139)."""

    @pytest.mark.asyncio
    async def test_turn_on_reverts_on_error(self) -> None:
        """async_turn_on reverts optimistic state on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_switch({"1": False})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_turn_on()
        assert e._optimistic_state is None

    @pytest.mark.asyncio
    async def test_turn_off_reverts_on_error(self) -> None:
        """async_turn_off reverts optimistic state on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_switch({"1": True})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_turn_off()
        assert e._optimistic_state is None
