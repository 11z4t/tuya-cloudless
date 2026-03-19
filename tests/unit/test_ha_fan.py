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
    entity._optimistic_is_on = None
    entity._optimistic_percentage = None
    entity._optimistic_preset_mode = None
    entity._optimistic_oscillating = None
    entity._optimistic_direction = None
    entity._restored_state = None
    entity.async_write_ha_state = MagicMock()
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

        coord = _make_coordinator()
        spec = _make_full_fan_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
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


class TestFanPercentageRangeMapping:
    """PLAT-713: Ceiling fan percentage range mapping for raw speed values 1-6."""

    def _spec(self) -> EntitySpec:
        """EntitySpec matching ceiling_fan.yaml: dp_value id=3, min_raw=1, max_raw=6."""
        return EntitySpec(
            platform="fan",
            name="ceiling_fan",
            dp_power=DPSpec(id="1", type="bool"),
            dp_value=DPSpec(id="3", type="int", min_raw=1, max_raw=6),
        )

    def test_percentage_maps_1_to_0(self) -> None:
        """Raw speed 1 (min_raw) must map to exactly 0 percent."""
        fan = _make_fan(dps={"3": 1}, spec=self._spec())
        assert fan.percentage == 0

    def test_percentage_maps_6_to_100(self) -> None:
        """Raw speed 6 (max_raw) must map to exactly 100 percent."""
        fan = _make_fan(dps={"3": 6}, spec=self._spec())
        assert fan.percentage == 100

    @pytest.mark.asyncio
    async def test_set_percentage_maps_50_to_3(self) -> None:
        """50 percent must map to raw speed 3 for the 1-6 range.

        Formula: min_raw + round(pct / 100 * (max_raw - min_raw))
               = 1 + round(50 / 100 * 5)
               = 1 + round(2.5)   # Python banker's rounding: round(2.5) == 2
               = 3
        """
        fan = _make_fan(dps={"1": True}, spec=self._spec())
        await fan.async_set_percentage(50)
        fan.coordinator.async_send_dps.assert_awaited_once_with({"3": 3})


class TestFanOptimisticState:
    """PLAT-719: optimistic state for fan commands."""

    @pytest.mark.asyncio
    async def test_turn_on_sets_optimistic_is_on(self) -> None:
        """async_turn_on sets _optimistic_is_on before the send completes."""
        e = _make_fan({"1": False})
        await e.async_turn_on()
        assert e._optimistic_is_on is True
        e.async_write_ha_state.assert_called()

    @pytest.mark.asyncio
    async def test_turn_off_sets_optimistic_is_on_false(self) -> None:
        """async_turn_off sets _optimistic_is_on=False before the send completes."""
        e = _make_fan({"1": True})
        await e.async_turn_off()
        assert e._optimistic_is_on is False
        e.async_write_ha_state.assert_called()

    @pytest.mark.asyncio
    async def test_set_percentage_sets_optimistic(self) -> None:
        """async_set_percentage sets _optimistic_percentage before the send."""
        e = _make_fan({"1": True})
        await e.async_set_percentage(60)
        assert e._optimistic_percentage == 60

    @pytest.mark.asyncio
    async def test_set_preset_mode_sets_optimistic(self) -> None:
        """async_set_preset_mode sets _optimistic_preset_mode before the send."""
        e = _make_fan({"1": True})
        await e.async_set_preset_mode("sleep")
        assert e._optimistic_preset_mode == "sleep"

    @pytest.mark.asyncio
    async def test_oscillate_sets_optimistic(self) -> None:
        """async_oscillate sets _optimistic_oscillating before the send."""
        e = _make_fan({"1": True})
        await e.async_oscillate(True)
        assert e._optimistic_oscillating is True

    @pytest.mark.asyncio
    async def test_set_direction_sets_optimistic(self) -> None:
        """async_set_direction sets _optimistic_direction before the send."""
        e = _make_fan({"1": True})
        await e.async_set_direction("reverse")
        assert e._optimistic_direction == "reverse"

    @pytest.mark.asyncio
    async def test_turn_on_with_percentage_sets_both_optimistic(self) -> None:
        """async_turn_on(percentage=…) sets both is_on and percentage optimistically."""
        e = _make_fan({"1": False})
        await e.async_turn_on(percentage=80)
        assert e._optimistic_is_on is True
        assert e._optimistic_percentage == 80

    @pytest.mark.asyncio
    async def test_turn_on_reverts_on_error(self) -> None:
        """async_turn_on reverts optimistic state when send raises HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_fan({"1": False})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_turn_on()
        assert e._optimistic_is_on is None

    @pytest.mark.asyncio
    async def test_set_percentage_reverts_on_error(self) -> None:
        """async_set_percentage reverts on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_fan({"1": True})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_set_percentage(60)
        assert e._optimistic_percentage is None

    @pytest.mark.asyncio
    async def test_set_preset_mode_reverts_on_error(self) -> None:
        """async_set_preset_mode reverts on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_fan({"1": True})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_set_preset_mode("auto")
        assert e._optimistic_preset_mode is None

    @pytest.mark.asyncio
    async def test_oscillate_reverts_on_error(self) -> None:
        """async_oscillate reverts on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_fan({"1": True})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_oscillate(True)
        assert e._optimistic_oscillating is None

    @pytest.mark.asyncio
    async def test_set_direction_reverts_on_error(self) -> None:
        """async_set_direction reverts on HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_fan({"1": True})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_set_direction("reverse")
        assert e._optimistic_direction is None

    def test_optimistic_wins_over_stale_dp(self) -> None:
        """Optimistic is_on=True takes priority even when live DP says False."""
        e = _make_fan({"1": False})
        e._optimistic_is_on = True
        assert e.is_on is True

    def test_percentage_optimistic_wins_over_stale_dp(self) -> None:
        """Optimistic percentage takes priority over live DP value."""
        e = _make_fan({"3": 1})  # raw=1 → 0%
        e._optimistic_percentage = 80
        assert e.percentage == 80

    def test_coordinator_update_clears_all_optimistic(self) -> None:
        """_handle_coordinator_update clears every optimistic attribute."""
        e = _make_fan({"1": True})
        e._optimistic_is_on = True
        e._optimistic_percentage = 60
        e._optimistic_preset_mode = "sleep"
        e._optimistic_oscillating = True
        e._optimistic_direction = "reverse"
        # Simulate coordinator update
        e.coordinator.data = e.coordinator.state
        e._handle_coordinator_update()
        assert e._optimistic_is_on is None
        assert e._optimistic_percentage is None
        assert e._optimistic_preset_mode is None
        assert e._optimistic_oscillating is None
        assert e._optimistic_direction is None


# ── Restore state tests ────────────────────────────────────────────────────────


# Helper: run the real async_added_to_hass with a mocked last state.
async def _run_restore(
    entity: TuyaCloudlessFan,
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


class TestFanRestoreState:
    @pytest.mark.asyncio
    async def test_restore_on_sets_optimistic_is_on(self) -> None:
        """Real async_added_to_hass: 'on' → optimistic_is_on=True."""
        from homeassistant.const import STATE_ON

        e = _make_fan({})
        await _run_restore(e, STATE_ON)
        assert e._optimistic_is_on is True
        assert e.is_on is True

    @pytest.mark.asyncio
    async def test_restore_off_sets_optimistic_is_off(self) -> None:
        """Real async_added_to_hass: 'off' → optimistic_is_on=False."""
        from homeassistant.const import STATE_OFF

        e = _make_fan({})
        await _run_restore(e, STATE_OFF)
        assert e._optimistic_is_on is False
        assert e.is_on is False

    @pytest.mark.asyncio
    async def test_restore_unknown_state_is_ignored(self) -> None:
        """Real async_added_to_hass: 'unavailable' must not touch optimistic."""
        e = _make_fan({})
        await _run_restore(e, "unavailable")
        assert e._optimistic_is_on is None

    @pytest.mark.asyncio
    async def test_restore_none_last_state_is_ignored(self) -> None:
        """Real async_added_to_hass: no recorded state → optimistic untouched."""
        e = _make_fan({})
        await _run_restore(e, None)
        assert e._optimistic_is_on is None

    @pytest.mark.asyncio
    async def test_restore_on_wins_over_stale_off_dp(self) -> None:
        """Restored 'on' via real method takes priority over stale off DP."""
        from homeassistant.const import STATE_ON

        e = _make_fan({"1": False})  # live DP says off
        await _run_restore(e, STATE_ON)
        assert e.is_on is True

    @pytest.mark.asyncio
    async def test_restore_off_wins_over_stale_on_dp(self) -> None:
        """Restored 'off' via real method takes priority over stale on DP."""
        from homeassistant.const import STATE_OFF

        e = _make_fan({"1": True})  # live DP says on
        await _run_restore(e, STATE_OFF)
        assert e.is_on is False

    @pytest.mark.asyncio
    async def test_coordinator_update_clears_restored_optimistic(self) -> None:
        """After coordinator update, live DP takes over from restored optimistic."""
        from homeassistant.const import STATE_ON

        e = _make_fan({"1": False})
        await _run_restore(e, STATE_ON)
        assert e.is_on is True

        e._optimistic_is_on = None  # cleared by _handle_coordinator_update
        assert e.is_on is False  # live DP wins

    @pytest.mark.asyncio
    async def test_restore_sets_restored_state_attribute(self) -> None:
        """RestoreStateMixin must populate _restored_state from last HA state."""
        from homeassistant.const import STATE_ON

        e = _make_fan({})
        await _run_restore(e, STATE_ON)
        assert e._restored_state == STATE_ON

    @pytest.mark.asyncio
    async def test_restore_off_restored_state_attribute(self) -> None:
        """_restored_state is 'off' after restoring an off fan."""
        from homeassistant.const import STATE_OFF

        e = _make_fan({})
        await _run_restore(e, STATE_OFF)
        assert e._restored_state == STATE_OFF

    def test_restored_state_initialised_to_none(self) -> None:
        """_restored_state must start as None before async_added_to_hass runs."""
        e = _make_fan({})
        assert e._restored_state is None


class TestFanRestoreExtraData:
    """PLAT-726: restore percentage, preset_mode, oscillating, direction from attributes."""

    @pytest.mark.asyncio
    async def test_restore_percentage(self) -> None:
        """percentage attribute is restored to _optimistic_percentage."""
        e = _make_fan({})
        await _run_restore(e, "on", {"percentage": 75})
        assert e._optimistic_percentage == 75

    @pytest.mark.asyncio
    async def test_restore_percentage_and_is_on_together(self) -> None:
        """Both is_on and percentage are restored from the same last state."""
        e = _make_fan({})
        await _run_restore(e, "on", {"percentage": 60})
        assert e._optimistic_is_on is True
        assert e._optimistic_percentage == 60

    @pytest.mark.asyncio
    async def test_restore_preset_mode(self) -> None:
        """A valid preset_mode attribute is restored to _optimistic_preset_mode."""
        e = _make_fan({})
        await _run_restore(e, "on", {"preset_mode": "sleep"})
        assert e._optimistic_preset_mode == "sleep"

    @pytest.mark.asyncio
    async def test_restore_invalid_preset_mode_ignored(self) -> None:
        """A preset_mode not in dp_options must be ignored (never set optimistic)."""
        e = _make_fan({})
        await _run_restore(e, "on", {"preset_mode": "turbo"})  # not in options
        assert e._optimistic_preset_mode is None

    @pytest.mark.asyncio
    async def test_restore_oscillating_true(self) -> None:
        """oscillating=True attribute is restored to _optimistic_oscillating."""
        e = _make_fan({})
        await _run_restore(e, "on", {"oscillating": True})
        assert e._optimistic_oscillating is True

    @pytest.mark.asyncio
    async def test_restore_oscillating_false(self) -> None:
        """oscillating=False (falsy but not None) must be restored correctly."""
        e = _make_fan({})
        await _run_restore(e, "on", {"oscillating": False})
        assert e._optimistic_oscillating is False

    @pytest.mark.asyncio
    async def test_restore_direction(self) -> None:
        """direction attribute is restored to _optimistic_direction."""
        e = _make_fan({})
        await _run_restore(e, "on", {"direction": "reverse"})
        assert e._optimistic_direction == "reverse"

    @pytest.mark.asyncio
    async def test_restore_percentage_skipped_when_no_dp_value(self) -> None:
        """percentage is NOT restored when the fan spec has no dp_value."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        await _run_restore(e, "on", {"percentage": 75})
        assert e._optimistic_percentage is None

    @pytest.mark.asyncio
    async def test_restore_empty_attributes_leaves_extras_none(self) -> None:
        """No saved attributes → all optimistic extras remain None after restore."""
        e = _make_fan({})
        await _run_restore(e, "on", {})
        assert e._optimistic_percentage is None
        assert e._optimistic_preset_mode is None
        assert e._optimistic_oscillating is None
        assert e._optimistic_direction is None


# ── Missing line coverage tests ────────────────────────────────────────────────


class TestFanIsOnDpPowerNone:
    """Line 153: is_on returns None when dp_power is None."""

    def test_is_on_none_when_dp_power_is_none(self) -> None:
        """is_on returns None when the spec has no dp_power (line 153)."""
        spec = EntitySpec(platform="fan", name="no_power_fan")
        e = _make_fan({"1": True}, spec=spec)
        e._spec = spec
        assert e.is_on is None


class TestFanRawToPercentageEdgeCases:
    """Lines 163, 168: _raw_to_percentage edge cases."""

    def test_raw_to_percentage_dp_none_returns_zero(self) -> None:
        """_raw_to_percentage returns 0 when dp_value is None (line 163)."""
        spec = EntitySpec(platform="fan", name="no_dp_fan")
        e = _make_fan({}, spec=spec)
        e._spec = spec
        assert e._raw_to_percentage(50) == 0

    def test_raw_to_percentage_span_zero_returns_100(self) -> None:
        """_raw_to_percentage returns 100 when min_raw==max_raw (span==0, line 168)."""
        spec = EntitySpec(
            platform="fan",
            name="span_zero_fan",
            dp_power=DPSpec(id="1", type="bool"),
            dp_value=DPSpec(id="3", type="int", min_raw=5, max_raw=5),
        )
        e = _make_fan({}, spec=spec)
        assert e._raw_to_percentage(5) == 100


class TestFanPercentageToRawDpNone:
    """Line 175: _percentage_to_raw returns percentage as-is when dp_value is None."""

    def test_percentage_to_raw_dp_none_returns_percentage(self) -> None:
        """_percentage_to_raw returns the percentage unchanged when dp is None (line 175)."""
        spec = EntitySpec(platform="fan", name="no_dp_fan")
        e = _make_fan({}, spec=spec)
        e._spec = spec
        assert e._percentage_to_raw(60) == 60


class TestFanPresetModeDpNoneAndOptimistic:
    """Lines 204, 206: preset_mode guard and optimistic path."""

    def test_preset_mode_none_when_dp_mode_is_none(self) -> None:
        """preset_mode returns None when spec has no dp_mode (line 204)."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        assert e.preset_mode is None

    def test_preset_mode_returns_optimistic_when_set(self) -> None:
        """preset_mode returns _optimistic_preset_mode when it is not None (line 206)."""
        e = _make_fan({"2": "sleep"})
        e._optimistic_preset_mode = "natural"
        assert e.preset_mode == "natural"


class TestFanOscillatingDpNoneAndOptimistic:
    """Lines 219, 221, 224: oscillating guard, optimistic, and live None."""

    def test_oscillating_none_when_dp_oscillate_is_none(self) -> None:
        """oscillating returns None when spec has no dp_oscillate (line 219)."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        assert e.oscillating is None

    def test_oscillating_returns_optimistic_when_set(self) -> None:
        """oscillating returns _optimistic_oscillating when it is not None (line 221)."""
        e = _make_fan({"8": False})
        e._optimistic_oscillating = True
        assert e.oscillating is True

    def test_oscillating_none_when_live_dp_returns_none(self) -> None:
        """oscillating returns None when live DP value is not present (line 224)."""
        e = _make_fan({})  # full spec, but no DP 8 value
        assert e.oscillating is None


class TestFanCurrentDirectionDpNoneAndOptimistic:
    """Lines 234, 236, 239: current_direction guard, optimistic, and live None."""

    def test_current_direction_none_when_dp_direction_is_none(self) -> None:
        """current_direction returns None when spec has no dp_direction (line 234)."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        assert e.current_direction is None

    def test_current_direction_returns_optimistic_when_set(self) -> None:
        """current_direction returns _optimistic_direction when not None (line 236)."""
        e = _make_fan({"4": "forward"})
        e._optimistic_direction = "reverse"
        assert e.current_direction == "reverse"

    def test_current_direction_none_when_live_dp_returns_none(self) -> None:
        """current_direction returns None when live DP value is absent (line 239)."""
        e = _make_fan({})  # full spec, but no DP 4 value
        assert e.current_direction is None


class TestFanTurnOnDpPowerNone:
    """Line 256: async_turn_on returns early when dp_power is None."""

    @pytest.mark.asyncio
    async def test_turn_on_noop_when_dp_power_is_none(self) -> None:
        """async_turn_on returns immediately when spec has no dp_power (line 256)."""
        spec = EntitySpec(platform="fan", name="no_power_fan")
        e = _make_fan({}, spec=spec)
        e._spec = spec
        await e.async_turn_on()
        e.coordinator.async_send_dps.assert_not_awaited()


class TestFanTurnOnWithPresetMode:
    """Lines 261, 267: async_turn_on with preset_mode argument."""

    @pytest.mark.asyncio
    async def test_turn_on_with_preset_mode_sets_optimistic(self) -> None:
        """async_turn_on(preset_mode=…) sets _optimistic_preset_mode (line 261)."""
        e = _make_fan({"1": False})
        await e.async_turn_on(preset_mode="sleep")
        assert e._optimistic_preset_mode == "sleep"

    @pytest.mark.asyncio
    async def test_turn_on_with_preset_mode_includes_dp_mode_in_dps(self) -> None:
        """async_turn_on(preset_mode=…) adds dp_mode to dps dict (line 267)."""
        e = _make_fan({"1": False})
        await e.async_turn_on(preset_mode="natural")
        call_args = e.coordinator.async_send_dps.call_args[0][0]
        assert call_args["2"] == "natural"
        assert call_args["1"] is True


class TestFanTurnOffDpPowerNone:
    """Line 280: async_turn_off returns early when dp_power is None."""

    @pytest.mark.asyncio
    async def test_turn_off_noop_when_dp_power_is_none(self) -> None:
        """async_turn_off returns immediately when spec has no dp_power (line 280)."""
        spec = EntitySpec(platform="fan", name="no_power_fan")
        e = _make_fan({}, spec=spec)
        e._spec = spec
        await e.async_turn_off()
        e.coordinator.async_send_dps.assert_not_awaited()


class TestFanTurnOffErrorPath:
    """Lines 285-288: async_turn_off error revert path."""

    @pytest.mark.asyncio
    async def test_turn_off_reverts_on_error(self) -> None:
        """async_turn_off reverts _optimistic_is_on on HomeAssistantError (lines 285-288)."""
        from homeassistant.exceptions import HomeAssistantError

        e = _make_fan({"1": True})
        e.coordinator.async_send_dps.side_effect = HomeAssistantError("send failed")
        with pytest.raises(HomeAssistantError):
            await e.async_turn_off()
        assert e._optimistic_is_on is None


class TestFanSetPercentageDpValueNone:
    """Line 297: async_set_percentage returns early when dp_value is None."""

    @pytest.mark.asyncio
    async def test_set_percentage_noop_when_dp_value_is_none(self) -> None:
        """async_set_percentage returns immediately when spec has no dp_value (line 297)."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        await e.async_set_percentage(60)
        e.coordinator.async_send_dps.assert_not_awaited()


class TestFanSetPresetModeDpModeNone:
    """Line 315: async_set_preset_mode returns early when dp_mode is None."""

    @pytest.mark.asyncio
    async def test_set_preset_mode_noop_when_dp_mode_is_none(self) -> None:
        """async_set_preset_mode returns immediately when spec has no dp_mode (line 315)."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        await e.async_set_preset_mode("sleep")
        e.coordinator.async_send_dps.assert_not_awaited()


class TestFanOscillateDpOscillateNone:
    """Line 332: async_oscillate returns early when dp_oscillate is None."""

    @pytest.mark.asyncio
    async def test_oscillate_noop_when_dp_oscillate_is_none(self) -> None:
        """async_oscillate returns immediately when spec has no dp_oscillate (line 332)."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        await e.async_oscillate(True)
        e.coordinator.async_send_dps.assert_not_awaited()


class TestFanSetDirectionDpDirectionNone:
    """Line 349: async_set_direction returns early when dp_direction is None."""

    @pytest.mark.asyncio
    async def test_set_direction_noop_when_dp_direction_is_none(self) -> None:
        """async_set_direction returns immediately when spec has no dp_direction (line 349)."""
        e = _make_fan({}, spec=_make_power_only_fan_spec())
        await e.async_set_direction("reverse")
        e.coordinator.async_send_dps.assert_not_awaited()


class TestFanDirectionValidation:
    """R20-5: async_set_direction must reject values other than forward/reverse."""

    @pytest.mark.asyncio
    async def test_invalid_direction_not_sent(self) -> None:
        """An invalid direction string must not be forwarded to the device."""
        e = _make_fan({"1": True})
        await e.async_set_direction("__invalid__")
        e.coordinator.async_send_dps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_direction_does_not_set_optimistic(self) -> None:
        """An invalid direction must not update the optimistic state."""
        e = _make_fan({"1": True})
        e._optimistic_direction = None
        await e.async_set_direction("__invalid__")
        assert e._optimistic_direction is None

    @pytest.mark.asyncio
    async def test_forward_accepted(self) -> None:
        """'forward' is a valid HA fan direction and must be sent."""
        e = _make_fan({"1": True})
        await e.async_set_direction("forward")
        e.coordinator.async_send_dps.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reverse_accepted(self) -> None:
        """'reverse' is a valid HA fan direction and must be sent."""
        e = _make_fan({"1": True})
        await e.async_set_direction("reverse")
        e.coordinator.async_send_dps.assert_awaited_once()
