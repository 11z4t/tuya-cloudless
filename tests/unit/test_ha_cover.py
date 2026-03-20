"""Tests for custom_components.tuya_cloudless.cover."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.cover import ATTR_POSITION, ATTR_TILT_POSITION, CoverEntityFeature
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(dps: dict[str, Any] | None = None) -> MagicMock:
    coord = MagicMock()
    coord._gw_id = "gw001"
    coord.gw_id = "gw001"
    coord.device_name = "gw001"
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = DeviceState(available=True, dps=dps or {})
    coord.async_send_dps = AsyncMock()
    return coord


def _make_cover_spec(
    dp_open_id: str = "1",
    dp_position_id: str | None = "2",
    dp_tilt_id: str | None = None,
    device_class: str | None = "blind",
) -> EntitySpec:
    return EntitySpec(
        platform="cover",
        name="cover",
        dp_open=DPSpec(id=dp_open_id, type="bool"),
        dp_position=DPSpec(id=dp_position_id, type="int") if dp_position_id else None,
        dp_tilt=DPSpec(id=dp_tilt_id, type="int") if dp_tilt_id else None,
        device_class=device_class,
    )


def _make_cover(
    dps: dict[str, Any] | None = None,
    spec: EntitySpec | None = None,
) -> TuyaCloudlessCover:
    coord = _make_coordinator(dps)
    _spec = spec or _make_cover_spec()
    entity = TuyaCloudlessCover.__new__(TuyaCloudlessCover)
    entity.coordinator = coord
    entity._dp_id = _spec.dp_open.id if _spec.dp_open else None
    entity._spec = _spec

    # Build features
    features = CoverEntityFeature(0)
    if _spec.dp_open is not None:
        features |= CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    if _spec.dp_position is not None:
        features |= CoverEntityFeature.SET_POSITION
    if _spec.dp_tilt is not None:
        features |= CoverEntityFeature.SET_TILT_POSITION
    entity._attr_supported_features = features
    entity._attr_unique_id = f"gw001_{_spec.platform}_{_spec.name}"
    entity._attr_translation_key = _spec.name
    # Optimistic state (set by __init__ normally)
    entity._optimistic_open = None
    entity._optimistic_position = None
    entity._optimistic_tilt = None
    entity._restored_state = None
    entity.async_write_ha_state = MagicMock()
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestCoverIsClosed:
    def test_closed_via_position_zero(self) -> None:
        e = _make_cover({"1": True, "2": 0})
        assert e.is_closed is True

    def test_open_via_position_nonzero(self) -> None:
        e = _make_cover({"1": True, "2": 50})
        assert e.is_closed is False

    def test_closed_via_open_dp_false(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover({"1": False}, spec=spec)
        assert e.is_closed is True

    def test_open_via_open_dp_true(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover({"1": True}, spec=spec)
        assert e.is_closed is False

    def test_none_when_no_data(self) -> None:
        e = _make_cover({})
        assert e.is_closed is None

    def test_none_when_position_dp_missing(self) -> None:
        e = _make_cover({"1": True})
        assert e.is_closed is None

    def test_none_when_open_dp_missing_no_position(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover({}, spec=spec)
        assert e.is_closed is None


class TestCoverPosition:
    def test_position_value(self) -> None:
        e = _make_cover({"2": 75})
        assert e.current_cover_position == 75

    def test_position_none_when_missing(self) -> None:
        e = _make_cover({})
        assert e.current_cover_position is None

    def test_position_none_when_no_spec(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover(spec=spec)
        assert e.current_cover_position is None


class TestCoverTilt:
    def test_tilt_position(self) -> None:
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({"3": 45}, spec=spec)
        assert e.current_cover_tilt_position == 45

    def test_tilt_none_when_no_spec(self) -> None:
        e = _make_cover()
        assert e.current_cover_tilt_position is None

    def test_tilt_none_when_missing(self) -> None:
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({}, spec=spec)
        assert e.current_cover_tilt_position is None


class TestCoverActions:
    @pytest.mark.asyncio
    async def test_open(self) -> None:
        e = _make_cover({"1": False})
        await e.async_open_cover()
        e.coordinator.async_send_dps.assert_awaited_once()
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert dps == {"1": True}

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        e = _make_cover({"1": True})
        await e.async_close_cover()
        e.coordinator.async_send_dps.assert_awaited_once()
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert dps == {"1": False}

    @pytest.mark.asyncio
    async def test_set_position(self) -> None:
        e = _make_cover({"2": 0})
        await e.async_set_cover_position(**{ATTR_POSITION: 50})
        e.coordinator.async_send_dps.assert_awaited_once()
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert dps == {"2": 50}

    @pytest.mark.asyncio
    async def test_set_position_noop_when_no_spec(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover(spec=spec)
        await e.async_set_cover_position(**{ATTR_POSITION: 50})
        e.coordinator.async_send_dps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_tilt(self) -> None:
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({"3": 0}, spec=spec)
        await e.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 45})
        e.coordinator.async_send_dps.assert_awaited_once()
        dps = e.coordinator.async_send_dps.call_args[0][0]
        assert dps == {"3": 45}

    @pytest.mark.asyncio
    async def test_set_tilt_noop_when_no_spec(self) -> None:
        e = _make_cover()
        await e.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 45})
        e.coordinator.async_send_dps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_open_noop_when_no_dp_open(self) -> None:
        spec = EntitySpec(platform="cover", name="noopen")
        e = _make_cover(spec=spec)
        e._spec = spec
        await e.async_open_cover()
        e.coordinator.async_send_dps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_close_noop_when_no_dp_open(self) -> None:
        spec = EntitySpec(platform="cover", name="noopen")
        e = _make_cover(spec=spec)
        e._spec = spec
        await e.async_close_cover()
        e.coordinator.async_send_dps.assert_not_awaited()


class TestCoverFeatures:
    def test_features_with_all(self) -> None:
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover(spec=spec)
        assert CoverEntityFeature.OPEN in e._attr_supported_features
        assert CoverEntityFeature.CLOSE in e._attr_supported_features
        assert CoverEntityFeature.SET_POSITION in e._attr_supported_features
        assert CoverEntityFeature.SET_TILT_POSITION in e._attr_supported_features

    def test_features_open_close_only(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover(spec=spec)
        assert CoverEntityFeature.OPEN in e._attr_supported_features
        assert CoverEntityFeature.SET_POSITION not in e._attr_supported_features

    def test_unique_id_format(self) -> None:
        e = _make_cover()
        assert e._attr_unique_id == "gw001_cover_cover"


class TestCoverSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.cover import async_setup_entry

        coord = _make_coordinator()
        spec = _make_cover_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Roller Blind",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessCover)

    @pytest.mark.asyncio
    async def test_setup_no_cover_specs_skips(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.cover import async_setup_entry

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
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 0


class TestCoverCoordinatorUpdate:
    """Tests for _handle_coordinator_update clearing optimistic (lines 144-147)."""

    def test_coordinator_update_clears_all_optimistic(self) -> None:
        """_handle_coordinator_update clears _optimistic_open, _position, _tilt."""
        e = _make_cover({"1": True, "2": 50})
        e._optimistic_open = True
        e._optimistic_position = 80
        e._optimistic_tilt = 45
        e.coordinator.data = e.coordinator.state
        e._handle_coordinator_update()
        assert e._optimistic_open is None
        assert e._optimistic_position is None
        assert e._optimistic_tilt is None


class TestCoverStop:
    """PLAT-774: Cover STOP support — tests for async_stop_cover and STOP feature flag."""

    def _make_cover_with_stop(self) -> TuyaCloudlessCover:
        """Return a cover entity that has dp_stop configured on DP 7."""
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        spec = EntitySpec(
            platform="cover",
            name="cover",
            dp_open=DPSpec(id="1", type="bool"),
            dp_position=DPSpec(id="2", type="int"),
            dp_stop=DPSpec(id="7", type="bool"),
        )
        coord = _make_coordinator({"1": True, "2": 50})
        return TuyaCloudlessCover(coord, spec)

    def _make_cover_without_stop(self) -> TuyaCloudlessCover:
        """Return a cover entity that does NOT have dp_stop configured."""
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        spec = _make_cover_spec()  # dp_stop is None by default
        coord = _make_coordinator({"1": True, "2": 50})
        return TuyaCloudlessCover(coord, spec)

    @pytest.mark.asyncio
    async def test_stop_cover_sends_dp7(self) -> None:
        """async_stop_cover sends True on the stop DP (id=7) when dp_stop is set."""
        e = self._make_cover_with_stop()
        await e.async_stop_cover()
        e.coordinator.async_send_dps.assert_awaited_once_with({"7": True})

    @pytest.mark.asyncio
    async def test_stop_cover_noop_when_no_dp_stop(self) -> None:
        """async_stop_cover does nothing when dp_stop is not configured in the profile."""
        e = self._make_cover_without_stop()
        await e.async_stop_cover()
        e.coordinator.async_send_dps.assert_not_awaited()

    def test_stop_feature_advertised_when_dp_stop_set(self) -> None:
        """CoverEntityFeature.STOP is in supported_features when dp_stop is configured."""
        e = self._make_cover_with_stop()
        assert CoverEntityFeature.STOP in e._attr_supported_features

    def test_stop_feature_absent_when_no_dp_stop(self) -> None:
        """CoverEntityFeature.STOP is not in supported_features without dp_stop."""
        e = self._make_cover_without_stop()
        assert CoverEntityFeature.STOP not in e._attr_supported_features


# ── Optimistic state tests ─────────────────────────────────────────────────────


class TestCoverOptimisticState:
    """Cover optimistic state: set before command, cleared by coordinator update."""

    @pytest.mark.asyncio
    async def test_open_cover_sets_optimistic_immediately(self) -> None:
        """async_open_cover sets optimistic state before awaiting the send."""
        e = _make_cover({"1": False, "2": 0})
        await e.async_open_cover()
        assert e._optimistic_open is True
        assert e._optimistic_position == 100
        e.async_write_ha_state.assert_called()
        assert e.is_closed is False

    @pytest.mark.asyncio
    async def test_close_cover_sets_optimistic_immediately(self) -> None:
        """async_close_cover sets optimistic closed state before awaiting the send."""
        e = _make_cover({"1": True, "2": 100})
        await e.async_close_cover()
        assert e._optimistic_open is False
        assert e._optimistic_position == 0
        assert e.is_closed is True

    @pytest.mark.asyncio
    async def test_set_position_sets_optimistic_immediately(self) -> None:
        """async_set_cover_position sets optimistic position before awaiting send."""
        e = _make_cover({"1": True, "2": 100})
        await e.async_set_cover_position(**{ATTR_POSITION: 50})
        assert e._optimistic_position == 50
        assert e._optimistic_open is True
        assert e.current_cover_position == 50

    @pytest.mark.asyncio
    async def test_set_tilt_sets_optimistic_immediately(self) -> None:
        """async_set_cover_tilt_position sets optimistic tilt before awaiting send."""
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({"3": 0}, spec=spec)
        await e.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 45})
        assert e._optimistic_tilt == 45
        assert e.current_cover_tilt_position == 45

    @pytest.mark.asyncio
    async def test_open_cover_reverts_optimistic_on_error(self) -> None:
        """async_open_cover reverts optimistic state when send raises HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_cover({"1": False, "2": 0})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_open_cover()
        assert e._optimistic_open is None
        assert e._optimistic_position is None
        assert e.async_write_ha_state.call_count == 2

    @pytest.mark.asyncio
    async def test_set_position_reverts_optimistic_on_error(self) -> None:
        """async_set_cover_position reverts on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_cover({"2": 100})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_set_cover_position(**{ATTR_POSITION: 50})
        assert e._optimistic_position is None
        assert e._optimistic_open is None

    def test_optimistic_wins_over_stale_dp(self) -> None:
        """Optimistic takes priority over stale live DP until coordinator clears it."""
        e = _make_cover({"1": False, "2": 0})  # DP says closed
        e._optimistic_open = True
        e._optimistic_position = 100
        assert e.is_closed is False
        assert e.current_cover_position == 100

    def test_coordinator_update_clears_optimistic(self) -> None:
        """After coordinator clears optimistic, live DP takes over."""
        e = _make_cover({"1": False, "2": 0})
        e._optimistic_open = True
        e._optimistic_position = 100
        # Simulate coordinator update clearing optimistic
        e._optimistic_open = None
        e._optimistic_position = None
        assert e.is_closed is True
        assert e.current_cover_position == 0


# ── Restore state tests ────────────────────────────────────────────────────────


# Helper: run the real async_added_to_hass with a mocked last state.
# Patches CoordinatorEntity.async_added_to_hass to avoid real HA setup;
# RestoreStateMixin and the platform method execute for real.
async def _run_restore(
    entity: TuyaCloudlessCover,
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


class TestCoverRestoreState:
    @pytest.mark.asyncio
    async def test_restore_open_sets_optimistic_open(self) -> None:
        """Real async_added_to_hass: 'open' → optimistic_open=True."""
        e = _make_cover({})
        await _run_restore(e, "open")
        assert e._optimistic_open is True
        assert e.is_closed is False

    @pytest.mark.asyncio
    async def test_restore_closed_sets_optimistic_closed_and_position(self) -> None:
        """Real async_added_to_hass: 'closed' → optimistic_open=False, position=0."""
        e = _make_cover({})
        await _run_restore(e, "closed")
        assert e._optimistic_open is False
        assert e._optimistic_position == 0
        assert e.is_closed is True

    @pytest.mark.asyncio
    async def test_restore_closed_no_position_dp(self) -> None:
        """Real async_added_to_hass: 'closed' without dp_position leaves position=None."""
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover({}, spec=spec)
        await _run_restore(e, "closed")
        assert e._optimistic_open is False
        assert e._optimistic_position is None
        assert e.is_closed is True

    @pytest.mark.asyncio
    async def test_restore_open_does_not_set_position(self) -> None:
        """Real async_added_to_hass: 'open' does not guess a position value."""
        e = _make_cover({})
        await _run_restore(e, "open")
        assert e._optimistic_open is True
        assert e._optimistic_position is None

    @pytest.mark.asyncio
    async def test_restore_unknown_state_is_ignored(self) -> None:
        """Real async_added_to_hass: 'unavailable' must not touch optimistic state."""
        e = _make_cover({})
        await _run_restore(e, "unavailable")
        assert e._optimistic_open is None
        assert e.is_closed is None

    @pytest.mark.asyncio
    async def test_restore_opening_state_is_ignored(self) -> None:
        """Real async_added_to_hass: transient 'opening' state must not restore."""
        e = _make_cover({})
        await _run_restore(e, "opening")
        assert e._optimistic_open is None

    @pytest.mark.asyncio
    async def test_restore_none_last_state_is_ignored(self) -> None:
        """Real async_added_to_hass: no recorded state → optimistic untouched."""
        e = _make_cover({})
        await _run_restore(e, None)
        assert e._optimistic_open is None
        assert e._optimistic_position is None

    @pytest.mark.asyncio
    async def test_restore_sets_restored_state_attribute(self) -> None:
        """RestoreStateMixin must populate _restored_state from last HA state."""
        e = _make_cover({})
        await _run_restore(e, "open")
        assert e._restored_state == "open"

    @pytest.mark.asyncio
    async def test_restore_closed_restored_state_attribute(self) -> None:
        """_restored_state is 'closed' after restoring a closed cover."""
        e = _make_cover({})
        await _run_restore(e, "closed")
        assert e._restored_state == "closed"

    def test_restored_state_initialised_to_none(self) -> None:
        """_restored_state must start as None before async_added_to_hass runs."""
        e = _make_cover({})
        assert e._restored_state is None


# ── Restore extra-data tests (PLAT-718: exact position + tilt) ─────────────────


class TestCoverDeviceClass:
    """Test device_class handling via the real constructor (line 96, 99-101)."""

    def test_valid_device_class_sets_attr(self) -> None:
        """Valid device_class string sets _attr_device_class via CoverDeviceClass."""
        from homeassistant.components.cover import CoverDeviceClass

        spec = _make_cover_spec(device_class="blind")
        coord = _make_coordinator()
        entity = TuyaCloudlessCover(coord, spec)
        assert entity._attr_device_class == CoverDeviceClass.BLIND

    def test_invalid_device_class_is_suppressed(self) -> None:
        """Invalid device_class does not crash — ValueError is suppressed."""
        spec = _make_cover_spec(device_class="not_a_real_class")
        coord = _make_coordinator()
        entity = TuyaCloudlessCover(coord, spec)
        assert getattr(entity, "_attr_device_class", None) != "not_a_real_class"

    def test_dp_tilt_adds_set_tilt_position_feature(self) -> None:
        """spec.dp_tilt sets SET_TILT_POSITION feature via real constructor (line 96)."""
        spec = _make_cover_spec(dp_tilt_id="3")
        coord = _make_coordinator()
        entity = TuyaCloudlessCover(coord, spec)
        assert CoverEntityFeature.SET_TILT_POSITION in entity._attr_supported_features


class TestCoverIsClosedNoSpec:
    """Line 175: is_closed returns None when neither dp_position nor dp_open are in spec."""

    def test_is_closed_none_when_no_dp_open_no_dp_position(self) -> None:
        """is_closed returns None when spec has neither dp_open nor dp_position (line 175)."""
        spec = EntitySpec(platform="cover", name="bare_cover")
        e = _make_cover(spec=spec)
        assert e.is_closed is None


class TestCoverCloseErrorPath:
    """Tests for async_close_cover error revert path (lines 240-244)."""

    @pytest.mark.asyncio
    async def test_close_cover_reverts_on_error(self) -> None:
        """async_close_cover reverts optimistic state on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_cover({"1": True, "2": 100})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_close_cover()
        assert e._optimistic_open is None
        assert e._optimistic_position is None


class TestCoverTiltErrorPath:
    """Tests for async_set_cover_tilt_position error revert path (lines 288-291)."""

    @pytest.mark.asyncio
    async def test_set_tilt_reverts_on_error(self) -> None:
        """async_set_cover_tilt_position reverts optimistic tilt on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({"3": 0}, spec=spec)
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 45})
        assert e._optimistic_tilt is None


class TestCoverRestoreExtraData:
    @pytest.mark.asyncio
    async def test_restore_exact_position_from_attributes(self) -> None:
        """Exact position from saved attributes overrides coarse open/closed."""
        e = _make_cover({})
        await _run_restore(e, "open", attributes={"current_position": 50})
        assert e._optimistic_position == 50
        assert e._optimistic_open is True  # 50 > 0

    @pytest.mark.asyncio
    async def test_restore_position_zero_from_attributes(self) -> None:
        """Position 0 from attributes marks cover as closed."""
        e = _make_cover({})
        await _run_restore(e, "closed", attributes={"current_position": 0})
        assert e._optimistic_position == 0
        assert e._optimistic_open is False

    @pytest.mark.asyncio
    async def test_restore_position_100_from_attributes(self) -> None:
        """Position 100 from attributes marks cover as fully open."""
        e = _make_cover({})
        await _run_restore(e, "open", attributes={"current_position": 100})
        assert e._optimistic_position == 100
        assert e._optimistic_open is True

    @pytest.mark.asyncio
    async def test_restore_tilt_from_attributes(self) -> None:
        """Tilt position is restored from saved attributes."""
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({}, spec=spec)
        attrs = {"current_position": 75, "current_tilt_position": 30}
        await _run_restore(e, "open", attributes=attrs)
        assert e._optimistic_tilt == 30

    @pytest.mark.asyncio
    async def test_restore_tilt_ignored_when_no_dp_tilt(self) -> None:
        """Tilt is not restored when dp_tilt is not in spec."""
        e = _make_cover({})  # default spec has no dp_tilt
        await _run_restore(e, "open", attributes={"current_tilt_position": 45})
        assert e._optimistic_tilt is None

    @pytest.mark.asyncio
    async def test_restore_position_ignored_when_no_dp_position(self) -> None:
        """Position is not restored when dp_position is not in spec."""
        spec = _make_cover_spec(dp_position_id=None)
        e = _make_cover({}, spec=spec)
        await _run_restore(e, "open", attributes={"current_position": 60})
        assert e._optimistic_position is None

    @pytest.mark.asyncio
    async def test_restore_position_invalid_value_is_ignored(self) -> None:
        """Non-integer position attribute must not crash or set optimistic."""
        e = _make_cover({})
        await _run_restore(e, "open", attributes={"current_position": "bad"})
        # Should fall back to coarse state (open → no position set)
        assert e._optimistic_position is None
        assert e._optimistic_open is True

    @pytest.mark.asyncio
    async def test_restore_position_overrides_coarse_closed_guess(self) -> None:
        """Position 35 in attributes overrides the coarse position=0 from 'closed'."""
        e = _make_cover({})
        # HA might report state as 'closed' but saved position=35 (partial close)
        await _run_restore(e, "closed", attributes={"current_position": 35})
        assert e._optimistic_position == 35
        assert e._optimistic_open is True  # 35 > 0


# ── R43 fixes ─────────────────────────────────────────────────────────────────


class TestCoverStopErrorHandlingR43:
    """R43-F2: async_stop_cover must propagate HomeAssistantError."""

    @pytest.mark.asyncio
    async def test_stop_cover_propagates_error(self) -> None:
        """HomeAssistantError from async_send_dp must propagate out of stop."""
        from unittest.mock import AsyncMock

        from homeassistant.exceptions import HomeAssistantError

        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        spec = EntitySpec(
            platform="cover",
            name="cover",
            dp_open=DPSpec(id="1", type="bool"),
            dp_stop=DPSpec(id="7", type="bool"),
        )
        coord = _make_coordinator({"1": True})
        coord.async_send_dps = AsyncMock(side_effect=HomeAssistantError("device unreachable"))
        entity = TuyaCloudlessCover(coord, spec)

        with pytest.raises(HomeAssistantError):
            await entity.async_stop_cover()


class TestCoverRestoreClampR43:
    """R43-F3: restored position/tilt must be clamped to [0, 100]."""

    @pytest.mark.asyncio
    async def test_restore_out_of_range_position_clamped_to_100(self) -> None:
        """Position > 100 from saved state is clamped to 100."""
        e = _make_cover({})
        await _run_restore(e, "open", attributes={"current_position": 200})
        assert e._optimistic_position == 100

    @pytest.mark.asyncio
    async def test_restore_negative_position_clamped_to_0(self) -> None:
        """Negative position from saved state is clamped to 0."""
        e = _make_cover({})
        await _run_restore(e, "open", attributes={"current_position": -10})
        assert e._optimistic_position == 0

    @pytest.mark.asyncio
    async def test_restore_out_of_range_tilt_clamped(self) -> None:
        """Tilt > 100 from saved state is clamped to 100."""
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({}, spec=spec)
        await _run_restore(e, "open", attributes={"current_tilt_position": 150})
        assert e._optimistic_tilt == 100


# ── R47 position clamping + OverflowError ─────────────────────────────────────


class TestCoverPositionClampR47:
    """R47-F5: current_cover_position must clamp live DP to [0, 100]."""

    def test_position_above_100_clamped(self) -> None:
        """Device sending 150 for position must be clamped to 100."""
        e = _make_cover({"2": 150})
        assert e.current_cover_position == 100

    def test_position_below_0_clamped(self) -> None:
        """Device sending -5 for position must be clamped to 0."""
        e = _make_cover({"2": -5})
        assert e.current_cover_position == 0

    def test_position_inf_returns_none(self) -> None:
        """int(float('inf')) raises OverflowError — must return None."""
        e = _make_cover({"2": float("inf")})
        assert e.current_cover_position is None

    def test_tilt_above_100_clamped(self) -> None:
        """Device sending 200 for tilt must be clamped to 100."""
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({"3": 200}, spec=spec)
        assert e.current_cover_tilt_position == 100

    def test_tilt_inf_returns_none(self) -> None:
        """int(float('inf')) raises OverflowError for tilt — must return None."""
        spec = _make_cover_spec(dp_tilt_id="3")
        e = _make_cover({"3": float("inf")}, spec=spec)
        assert e.current_cover_tilt_position is None
