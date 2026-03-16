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


class TestCoverStop:
    """PLAT-714: Cover STOP support — tests for async_stop_cover and STOP feature flag."""

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
