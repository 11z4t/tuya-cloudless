"""Tests for platform async_setup_entry functions and entity constructors.

These tests verify that entity creation from device profiles works correctly,
covering the constructor logic and property setups.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from tuya_cloudless.profiles import DPSpec, EntitySpec  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_runtime(entity_specs: list[EntitySpec]) -> MagicMock:
    from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData

    coord = MagicMock()
    coord._gw_id = "gw001"
    coord.gw_id = "gw001"
    coord.device_name = "gw001"
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = MagicMock()
    coord.state.available = True
    coord.state.dps = {}
    coord.async_send_dps = AsyncMock()

    runtime = TuyaCloudlessRuntimeData(
        coordinator=coord,
        device_info=MagicMock(),
        entity_specs=tuple(entity_specs),
        profile_name="Test Profile",
    )
    return runtime


def _make_entry(entity_specs: list[EntitySpec]) -> MagicMock:
    entry = MagicMock()
    entry.runtime_data = _make_runtime(entity_specs)
    return entry


# ── Switch setup_entry ─────────────────────────────────────────────────────────


class TestSwitchSetup:
    @pytest.mark.asyncio
    async def test_setup_entry_creates_entities(self) -> None:
        from custom_components.tuya_cloudless.switch import async_setup_entry

        spec = EntitySpec(
            platform="switch", name="main_switch", dp_power=DPSpec(id="1", type="bool")
        )
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_setup_entry_skips_non_switch(self) -> None:
        from custom_components.tuya_cloudless.switch import async_setup_entry

        spec = EntitySpec(platform="sensor", name="power", dp_value=DPSpec(id="19", type="int"))
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 0

    def test_switch_constructor(self) -> None:
        from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(
            platform="switch", name="main_switch", dp_power=DPSpec(id="1", type="bool")
        )
        switch = TuyaCloudlessSwitch(coord, spec)
        assert switch._attr_unique_id == "gw001_switch_main_switch"
        assert switch._dp_id == "1"

    def test_switch_constructor_no_dp_power_defaults_to_1(self) -> None:
        from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(platform="switch", name="fallback", dp_power=None)
        switch = TuyaCloudlessSwitch(coord, spec)
        assert switch._dp_id == "1"

    def test_switch_is_on_property(self) -> None:
        from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

        coord = _make_runtime([]).coordinator
        coord.state.dps = {"1": True}
        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        switch = TuyaCloudlessSwitch(coord, spec)
        assert switch.is_on is True


# ── Sensor setup_entry ─────────────────────────────────────────────────────────


class TestSensorSetup:
    @pytest.mark.asyncio
    async def test_setup_entry_creates_profile_sensors_and_diagnostics(self) -> None:
        from custom_components.tuya_cloudless.sensor import async_setup_entry

        spec = EntitySpec(
            platform="sensor",
            name="power_consumption",
            dp_value=DPSpec(id="19", type="int", scale=0.1),
            device_class="power",
            state_class="measurement",
            unit="W",
        )
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        # 1 profile sensor + 2 diagnostic sensors (last_seen, reconnects)
        assert len(added) == 3

    @pytest.mark.asyncio
    async def test_setup_entry_creates_only_diagnostics_when_no_sensors(self) -> None:
        from custom_components.tuya_cloudless.sensor import async_setup_entry

        entry = _make_entry([])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 2  # always: last_seen + reconnects

    def test_sensor_constructor_with_valid_device_class(self) -> None:
        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(
            platform="sensor",
            name="power",
            dp_value=DPSpec(id="19", type="int", scale=0.1),
            device_class="power",
            state_class="measurement",
            unit="W",
        )
        sensor = TuyaCloudlessSensor(coord, spec)
        assert sensor._attr_native_unit_of_measurement == "W"

    def test_sensor_constructor_invalid_device_class_logs_warning(self) -> None:
        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(
            platform="sensor",
            name="weird",
            dp_value=DPSpec(id="5", type="int"),
            device_class="not_a_real_class",
            state_class="not_a_real_state_class",
        )
        # Should not raise — just logs a warning
        sensor = TuyaCloudlessSensor(coord, spec)
        assert sensor is not None

    def test_last_seen_sensor_native_value(self) -> None:
        from datetime import UTC, datetime

        from custom_components.tuya_cloudless.sensor import TuyaLastSeenSensor

        coord = _make_runtime([]).coordinator
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        coord.state.last_seen = ts
        sensor = TuyaLastSeenSensor(coord)
        assert sensor.native_value == ts.isoformat()

    def test_last_seen_sensor_native_value_none(self) -> None:
        from custom_components.tuya_cloudless.sensor import TuyaLastSeenSensor

        coord = _make_runtime([]).coordinator
        coord.state.last_seen = None
        sensor = TuyaLastSeenSensor(coord)
        assert sensor.native_value is None

    def test_reconnect_sensor_native_value(self) -> None:
        from custom_components.tuya_cloudless.sensor import TuyaReconnectSensor

        coord = _make_runtime([]).coordinator
        coord.state.reconnect_count = 7
        sensor = TuyaReconnectSensor(coord)
        assert sensor.native_value == 7

    def test_reconnect_sensor_disabled_by_default(self) -> None:
        from homeassistant.const import EntityCategory

        from custom_components.tuya_cloudless.sensor import TuyaReconnectSensor

        coord = _make_runtime([]).coordinator
        coord.state.reconnect_count = 0
        sensor = TuyaReconnectSensor(coord)
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC
        assert sensor._attr_entity_registry_enabled_default is False


# ── BinarySensor setup_entry ───────────────────────────────────────────────────


class TestBinarySensorSetup:
    @pytest.mark.asyncio
    async def test_setup_entry_creates_entities(self) -> None:
        from custom_components.tuya_cloudless.binary_sensor import async_setup_entry

        spec = EntitySpec(
            platform="binary_sensor",
            name="overload",
            dp_power=DPSpec(id="26", type="bool"),
            device_class="problem",
        )
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_setup_entry_skips_non_binary_sensor(self) -> None:
        from custom_components.tuya_cloudless.binary_sensor import async_setup_entry

        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 0

    def test_binary_sensor_constructor(self) -> None:
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(
            platform="binary_sensor",
            name="overload",
            dp_power=DPSpec(id="26", type="bool"),
            device_class="problem",
        )
        sensor = TuyaCloudlessBinarySensor(coord, spec)
        assert sensor._attr_unique_id == "gw001_binary_sensor_overload"
        assert sensor._dp_id == "26"

    def test_binary_sensor_is_on_true(self) -> None:
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        coord = _make_runtime([]).coordinator
        coord.state.dps = {"26": True}
        spec = EntitySpec(
            platform="binary_sensor",
            name="overload",
            dp_power=DPSpec(id="26", type="bool"),
            device_class="problem",
        )
        sensor = TuyaCloudlessBinarySensor(coord, spec)
        assert sensor.is_on is True

    def test_binary_sensor_invalid_device_class_logs_warning(self) -> None:
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(
            platform="binary_sensor",
            name="weird",
            dp_power=DPSpec(id="1", type="bool"),
            device_class="not_real",
        )
        sensor = TuyaCloudlessBinarySensor(coord, spec)
        assert sensor is not None


# ── Cover setup_entry ──────────────────────────────────────────────────────────


class TestCoverSetup:
    @pytest.mark.asyncio
    async def test_setup_entry_creates_entities(self) -> None:
        from custom_components.tuya_cloudless.cover import async_setup_entry

        spec = EntitySpec(
            platform="cover",
            name="cover",
            dp_open=DPSpec(id="1", type="bool"),
            dp_position=DPSpec(id="2", type="int"),
            device_class="blind",
        )
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_setup_entry_skips_non_cover(self) -> None:
        from custom_components.tuya_cloudless.cover import async_setup_entry

        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 0

    def test_cover_constructor(self) -> None:
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(
            platform="cover",
            name="cover",
            dp_open=DPSpec(id="1", type="bool"),
            dp_position=DPSpec(id="2", type="int"),
            dp_tilt=DPSpec(id="3", type="int"),
            device_class="blind",
        )
        cover = TuyaCloudlessCover(coord, spec)
        assert cover._attr_unique_id == "gw001_cover_cover"

        from homeassistant.components.cover import CoverEntityFeature

        assert CoverEntityFeature.OPEN in cover._attr_supported_features
        assert CoverEntityFeature.SET_POSITION in cover._attr_supported_features
        assert CoverEntityFeature.SET_TILT_POSITION in cover._attr_supported_features

    def test_cover_is_closed_fallback_when_no_position(self) -> None:
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        coord = _make_runtime([]).coordinator
        coord.state.dps = {"1": False}
        spec = EntitySpec(
            platform="cover",
            name="cover",
            dp_open=DPSpec(id="1", type="bool"),
            dp_position=None,
            device_class="blind",
        )
        cover = TuyaCloudlessCover(coord, spec)
        assert cover.is_closed is True

    def test_cover_is_closed_none_when_no_dp(self) -> None:
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        coord = _make_runtime([]).coordinator
        coord.state.dps = {}
        spec = EntitySpec(
            platform="cover",
            name="cover",
            dp_open=None,
            dp_position=None,
            device_class="blind",
        )
        cover = TuyaCloudlessCover(coord, spec)
        assert cover.is_closed is None

    def test_cover_invalid_device_class_suppressed(self) -> None:
        from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

        coord = _make_runtime([]).coordinator
        spec = EntitySpec(
            platform="cover",
            name="cover",
            dp_open=DPSpec(id="1", type="bool"),
            device_class="not_real",
        )
        cover = TuyaCloudlessCover(coord, spec)
        assert cover is not None


# ── Light setup_entry ──────────────────────────────────────────────────────────


class TestLightSetup:
    @pytest.mark.asyncio
    async def test_setup_entry_creates_light(self) -> None:
        from custom_components.tuya_cloudless.light import async_setup_entry

        spec = EntitySpec(
            platform="light",
            name="main_light",
            dp_power=DPSpec(id="1", type="bool"),
        )
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_setup_entry_skips_non_light(self) -> None:
        from custom_components.tuya_cloudless.light import async_setup_entry

        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        entry = _make_entry([spec])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 0

    @pytest.mark.asyncio
    async def test_setup_entry_no_specs_skips(self) -> None:
        from custom_components.tuya_cloudless.light import async_setup_entry

        entry = _make_entry([])
        added: list = []
        await async_setup_entry(MagicMock(), entry, added.extend)
        assert len(added) == 0
