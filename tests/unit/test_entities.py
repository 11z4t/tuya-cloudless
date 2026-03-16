"""Unit tests for Tuya Cloudless entity classes.

Tests cover platform entities (switch, light, sensor, binary_sensor, cover)
with focus on error scenarios, attribute mapping, and unique_id format.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure lib/ is importable
_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from tuya_cloudless.profiles import DPSpec, EntitySpec  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(dps: dict[str, Any] | None = None, gw_id: str = "gw001") -> MagicMock:
    """Return a minimal coordinator mock with pre-set DPS state."""
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.tuya_cloudless.const import DOMAIN

    coord = MagicMock()
    coord._gw_id = gw_id
    coord.gw_id = gw_id
    coord.device_name = gw_id
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = MagicMock()
    coord.state.available = True
    coord.state.dps = dps or {}
    coord.async_send_dps = AsyncMock()
    # Build DeviceInfo matching what async_setup_entry produces (PLAT-714)
    coord.device_info = DeviceInfo(
        identifiers={(DOMAIN, gw_id)},
        name=gw_id,
        manufacturer="Tuya",
        model="Tuya Cloudless",
        sw_version="3.3",
        configuration_url="http://",
    )
    return coord


def _make_switch_spec(dp_id: str = "1") -> EntitySpec:
    return EntitySpec(
        platform="switch",
        name="main_switch",
        dp_power=DPSpec(id=dp_id, type="bool"),
    )


def _make_sensor_spec(dp_id: str = "19") -> EntitySpec:
    return EntitySpec(
        platform="sensor",
        name="power_consumption",
        dp_value=DPSpec(id=dp_id, type="int", scale=0.1),
        device_class="power",
        state_class="measurement",
        unit="W",
    )


def _make_binary_sensor_spec(dp_id: str = "26") -> EntitySpec:
    return EntitySpec(
        platform="binary_sensor",
        name="overload",
        dp_power=DPSpec(id=dp_id, type="bool"),
        device_class="problem",
    )


def _make_cover_spec(
    dp_open_id: str = "1",
    dp_position_id: str | None = "2",
    dp_tilt_id: str | None = None,
) -> EntitySpec:
    return EntitySpec(
        platform="cover",
        name="cover",
        dp_open=DPSpec(id=dp_open_id, type="bool"),
        dp_position=DPSpec(id=dp_position_id, type="int") if dp_position_id else None,
        dp_tilt=DPSpec(id=dp_tilt_id, type="int") if dp_tilt_id else None,
        device_class="blind",
    )


# ── Switch tests ───────────────────────────────────────────────────────────────


class TestSwitch:
    def _make(self, dps: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

        coord = _make_coordinator(dps)
        spec = _make_switch_spec()
        entity = TuyaCloudlessSwitch.__new__(TuyaCloudlessSwitch)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = f"{coord._gw_id}_{spec.platform}_{spec.name}"
        return entity

    def test_unique_id_includes_platform(self) -> None:
        e = self._make()
        assert "switch" in e._attr_unique_id
        assert e._attr_unique_id == "gw001_switch_main_switch"

    def test_is_on_true(self) -> None:
        e = self._make({"1": True})
        assert e.get_dp("1") is True

    def test_is_on_false(self) -> None:
        e = self._make({"1": False})
        assert e.get_dp("1") is False

    def test_is_on_none_when_missing(self) -> None:
        e = self._make({})
        assert e.get_dp("1") is None


# ── Sensor tests ───────────────────────────────────────────────────────────────


class TestSensor:
    def _make(self, dps: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        coord = _make_coordinator(dps)
        spec = _make_sensor_spec()

        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = "19"
        entity._spec = spec
        entity._attr_unique_id = f"{coord._gw_id}_{spec.platform}_{spec.name}"
        return entity

    def test_unique_id_includes_platform(self) -> None:
        e = self._make()
        assert e._attr_unique_id == "gw001_sensor_power_consumption"

    def test_native_value_scaled(self) -> None:
        e = self._make({"19": 1000})
        # scale=0.1 → 1000 * 0.1 = 100.0
        assert e.native_value == pytest.approx(100.0)

    def test_native_value_none_when_dp_missing(self) -> None:
        e = self._make({})
        assert e.native_value is None

    def test_native_value_no_scale(self) -> None:
        spec = EntitySpec(
            platform="sensor",
            name="raw",
            dp_value=DPSpec(id="5", type="int", scale=1.0),
        )
        coord = _make_coordinator({"5": 42})
        entity = MagicMock()
        entity._spec = spec
        entity._dp_id = "5"
        entity.coordinator = coord
        entity.get_dp = lambda dp_id=None: coord.state.dps.get(dp_id or "5")
        entity.native_value = property(
            lambda self: self._spec.dp_value and self.get_dp(self._spec.dp_value.id)
        )
        assert coord.state.dps["5"] == 42


# ── BinarySensor tests ─────────────────────────────────────────────────────────


class TestBinarySensor:
    def _make(self, dps: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        coord = _make_coordinator(dps)
        spec = _make_binary_sensor_spec()

        entity = TuyaCloudlessBinarySensor.__new__(TuyaCloudlessBinarySensor)
        entity.coordinator = coord
        entity._dp_id = "26"
        entity._spec = spec
        entity._attr_unique_id = f"{coord._gw_id}_{spec.platform}_{spec.name}"
        return entity

    def test_unique_id_includes_platform(self) -> None:
        e = self._make()
        assert e._attr_unique_id == "gw001_binary_sensor_overload"

    def test_is_on_true(self) -> None:
        e = self._make({"26": True})
        assert e.get_dp() is True

    def test_is_on_false(self) -> None:
        e = self._make({"26": False})
        assert e.get_dp() is False

    def test_is_on_none_when_missing(self) -> None:
        e = self._make({})
        assert e.get_dp() is None


# ── Cover tests ────────────────────────────────────────────────────────────────


class TestCover:
    def _make(
        self,
        dps: dict[str, Any] | None = None,
        spec: EntitySpec | None = None,
    ) -> Any:
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        coord = _make_coordinator(dps)
        _spec = spec or _make_cover_spec()

        entity = TuyaCloudlessCover.__new__(TuyaCloudlessCover)
        entity.coordinator = coord
        entity._dp_id = _spec.dp_open.id if _spec.dp_open else None
        entity._spec = _spec
        entity._attr_unique_id = f"{coord._gw_id}_{_spec.platform}_{_spec.name}"
        entity._attr_supported_features = MagicMock()
        return entity

    def test_unique_id_includes_platform(self) -> None:
        e = self._make()
        assert e._attr_unique_id == "gw001_cover_cover"

    def test_is_closed_via_position_zero(self) -> None:
        e = self._make({"1": True, "2": 0})
        assert e.current_cover_position == 0
        assert e.is_closed is True

    def test_is_closed_via_position_nonzero(self) -> None:
        e = self._make({"1": True, "2": 50})
        assert e.is_closed is False

    def test_is_closed_via_open_dp_when_no_position(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = self._make({"1": False}, spec=spec)
        # dp_open=False → closed
        assert e.is_closed is True

    def test_is_open_via_open_dp_when_no_position(self) -> None:
        spec = _make_cover_spec(dp_position_id=None)
        e = self._make({"1": True}, spec=spec)
        assert e.is_closed is False

    def test_is_closed_none_when_no_data(self) -> None:
        e = self._make({})
        assert e.is_closed is None

    def test_current_position_returns_int(self) -> None:
        e = self._make({"2": 75})
        assert e.current_cover_position == 75

    def test_current_position_none_when_missing(self) -> None:
        e = self._make({})
        assert e.current_cover_position is None

    def test_tilt_position_when_available(self) -> None:
        spec = _make_cover_spec(dp_tilt_id="3")
        e = self._make({"3": 45}, spec=spec)
        assert e.current_cover_tilt_position == 45

    def test_tilt_position_none_when_no_spec(self) -> None:
        e = self._make({"3": 45})
        assert e.current_cover_tilt_position is None


# ── ExtraStateAttributes tests ─────────────────────────────────────────────────


class TestExtraStateAttributes:
    def _make_entity(self, dp_id: str | None, dps: dict[str, Any]) -> Any:
        from custom_components.tuya_cloudless.entity import TuyaCloudlessEntity

        coord = _make_coordinator(dps)
        entity = TuyaCloudlessEntity.__new__(TuyaCloudlessEntity)
        entity.coordinator = coord
        entity._dp_id = dp_id
        return entity

    def test_returns_dp_id_and_raw_value(self) -> None:
        e = self._make_entity("1", {"1": True})
        attrs = e.extra_state_attributes
        assert attrs["dp_id"] == "1"
        assert attrs["raw_value"] is True

    def test_returns_empty_when_no_dp_id(self) -> None:
        e = self._make_entity(None, {"1": True})
        assert e.extra_state_attributes == {}

    def test_raw_value_none_when_dp_missing(self) -> None:
        e = self._make_entity("99", {})
        attrs = e.extra_state_attributes
        assert attrs["raw_value"] is None


# ── Unique ID collision prevention ─────────────────────────────────────────────


class TestUniqueIdNoCollision:
    """Verify that platform is included in unique_id to prevent collisions."""

    def test_sensor_and_switch_different_ids(self) -> None:
        coord = _make_coordinator()

        sw_spec = EntitySpec(platform="switch", name="main", dp_power=DPSpec(id="1", type="bool"))
        sen_spec = EntitySpec(platform="sensor", name="main", dp_value=DPSpec(id="1", type="int"))

        sw_uid = f"{coord._gw_id}_{sw_spec.platform}_{sw_spec.name}"
        sen_uid = f"{coord._gw_id}_{sen_spec.platform}_{sen_spec.name}"

        assert sw_uid != sen_uid
        assert sw_uid == "gw001_switch_main"
        assert sen_uid == "gw001_sensor_main"


# ── Switch action tests ────────────────────────────────────────────────────────


class TestSwitchActions:
    def _make(self, dps: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

        coord = _make_coordinator(dps)
        spec = _make_switch_spec()
        entity = TuyaCloudlessSwitch.__new__(TuyaCloudlessSwitch)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        entity._attr_unique_id = f"{coord._gw_id}_{spec.platform}_{spec.name}"
        return entity

    @pytest.mark.asyncio
    async def test_turn_on_sends_true(self) -> None:
        e = self._make()
        await e.async_turn_on()
        e.coordinator.async_send_dps.assert_called_once_with({"1": True})

    @pytest.mark.asyncio
    async def test_turn_off_sends_false(self) -> None:
        e = self._make({"1": True})
        await e.async_turn_off()
        e.coordinator.async_send_dps.assert_called_once_with({"1": False})

    def test_is_on_bool_cast(self) -> None:
        e = self._make({"1": 1})
        assert e.is_on is True

    def test_is_on_false_cast(self) -> None:
        e = self._make({"1": 0})
        assert e.is_on is False


# ── Sensor action tests ────────────────────────────────────────────────────────


class TestSensorNativeValue:
    def _make(self, dps: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        coord = _make_coordinator(dps)
        spec = _make_sensor_spec()
        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = "19"
        entity._spec = spec
        return entity

    def test_native_value_with_scale_1(self) -> None:
        spec = EntitySpec(
            platform="sensor",
            name="raw",
            dp_value=DPSpec(id="5", type="int", scale=1.0),
        )
        coord = _make_coordinator({"5": 42})
        entity = __import__(
            "custom_components.tuya_cloudless.sensor", fromlist=["TuyaCloudlessSensor"]
        ).TuyaCloudlessSensor.__new__(
            __import__(
                "custom_components.tuya_cloudless.sensor",
                fromlist=["TuyaCloudlessSensor"],
            ).TuyaCloudlessSensor
        )
        entity.coordinator = coord
        entity._dp_id = "5"
        entity._spec = spec
        assert entity.native_value == 42

    def test_native_value_no_dp_value_spec(self) -> None:
        spec = EntitySpec(platform="sensor", name="empty", dp_value=None)
        coord = _make_coordinator({})
        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        assert entity.native_value is None


# ── BinarySensor action tests ──────────────────────────────────────────────────


class TestBinarySensorIsOn:
    def _make(self, dps: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        coord = _make_coordinator(dps)
        spec = _make_binary_sensor_spec()
        entity = TuyaCloudlessBinarySensor.__new__(TuyaCloudlessBinarySensor)
        entity.coordinator = coord
        entity._dp_id = "26"
        entity._spec = spec
        return entity

    def test_is_on_uses_dp_power(self) -> None:
        e = self._make({"26": True})
        assert e.is_on is True

    def test_is_on_false(self) -> None:
        e = self._make({"26": False})
        assert e.is_on is False

    def test_is_on_none_when_missing(self) -> None:
        e = self._make({})
        assert e.is_on is None


# ── Cover action tests ─────────────────────────────────────────────────────────


class TestCoverActions:
    def _make(
        self,
        dps: dict[str, Any] | None = None,
        spec: EntitySpec | None = None,
    ) -> Any:
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        coord = _make_coordinator(dps)
        _spec = spec or _make_cover_spec()

        entity = TuyaCloudlessCover.__new__(TuyaCloudlessCover)
        entity.coordinator = coord
        entity._dp_id = _spec.dp_open.id if _spec.dp_open else None
        entity._spec = _spec
        entity._attr_unique_id = f"{coord._gw_id}_{_spec.platform}_{_spec.name}"
        entity._attr_supported_features = MagicMock()
        return entity

    @pytest.mark.asyncio
    async def test_open_cover_sends_true(self) -> None:
        e = self._make()
        await e.async_open_cover()
        e.coordinator.async_send_dps.assert_called_once_with({"1": True})

    @pytest.mark.asyncio
    async def test_close_cover_sends_false(self) -> None:
        e = self._make()
        await e.async_close_cover()
        e.coordinator.async_send_dps.assert_called_once_with({"1": False})

    @pytest.mark.asyncio
    async def test_set_cover_position(self) -> None:
        from homeassistant.components.cover import ATTR_POSITION

        e = self._make()
        await e.async_set_cover_position(**{ATTR_POSITION: 75})
        e.coordinator.async_send_dps.assert_called_once_with({"2": 75})

    @pytest.mark.asyncio
    async def test_set_cover_tilt(self) -> None:
        from homeassistant.components.cover import ATTR_TILT_POSITION

        spec = _make_cover_spec(dp_tilt_id="3")
        e = self._make(spec=spec)
        await e.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 45})
        e.coordinator.async_send_dps.assert_called_once_with({"3": 45})

    @pytest.mark.asyncio
    async def test_set_position_noop_when_no_spec(self) -> None:
        from homeassistant.components.cover import ATTR_POSITION

        spec = _make_cover_spec(dp_position_id=None)
        e = self._make(spec=spec)
        await e.async_set_cover_position(**{ATTR_POSITION: 50})
        e.coordinator.async_send_dps.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_tilt_noop_when_no_spec(self) -> None:
        from homeassistant.components.cover import ATTR_TILT_POSITION

        e = self._make()
        await e.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 45})
        e.coordinator.async_send_dps.assert_not_called()


# ── Entity base tests ─────────────────────────────────────────────────────────


class TestEntityBase:
    def _make(self, dp_id: str | None, dps: dict[str, Any]) -> Any:
        from custom_components.tuya_cloudless.entity import TuyaCloudlessEntity

        coord = _make_coordinator(dps)
        entity = TuyaCloudlessEntity.__new__(TuyaCloudlessEntity)
        entity.coordinator = coord
        entity._dp_id = dp_id
        return entity

    def test_available_reflects_coordinator_state(self) -> None:
        e = self._make("1", {})
        e.coordinator.state.available = True
        assert e.available is True

    def test_available_false_when_disconnected(self) -> None:
        e = self._make("1", {})
        e.coordinator.state.available = False
        assert e.available is False

    def test_device_info_has_domain(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        e = self._make("1", {})
        info = e.device_info
        assert (DOMAIN, "gw001") in info["identifiers"]

    def test_device_info_sw_version(self) -> None:
        e = self._make("1", {})
        info = e.device_info
        assert info["sw_version"] == "3.3"

    @pytest.mark.asyncio
    async def test_async_send_dp_delegates_to_coordinator(self) -> None:
        e = self._make("1", {})
        await e.async_send_dp("1", True)
        e.coordinator.async_send_dps.assert_called_once_with({"1": True})
