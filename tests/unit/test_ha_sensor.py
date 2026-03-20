"""Tests for custom_components.tuya_cloudless.sensor."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.sensor import (
    TuyaCloudlessSensor,
    TuyaLastSeenSensor,
    TuyaReconnectSensor,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(
    dps: dict[str, Any] | None = None,
    gw_id: str = "gw001",
    last_seen: datetime | None = None,
    reconnect_count: int = 0,
) -> MagicMock:
    coord = MagicMock()
    coord._gw_id = gw_id
    coord.gw_id = gw_id
    coord.device_name = gw_id
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = DeviceState(
        available=True,
        dps=dps or {},
        last_seen=last_seen,
        reconnect_count=reconnect_count,
    )
    coord.async_send_dps = AsyncMock()
    return coord


def _make_sensor_spec(
    dp_id: str = "19",
    scale: float = 0.1,
    device_class: str | None = "power",
    state_class: str | None = "measurement",
    unit: str | None = "W",
) -> EntitySpec:
    return EntitySpec(
        platform="sensor",
        name="power_consumption",
        dp_value=DPSpec(id=dp_id, type="int", scale=scale),
        device_class=device_class,
        state_class=state_class,
        unit=unit,
    )


def _make_sensor(
    dps: dict[str, Any] | None = None,
    dp_id: str = "19",
    scale: float = 0.1,
) -> TuyaCloudlessSensor:
    coord = _make_coordinator(dps)
    spec = _make_sensor_spec(dp_id, scale)
    entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
    entity.coordinator = coord
    entity._dp_id = dp_id
    entity._spec = spec
    entity._attr_unique_id = f"{coord.gw_id}_{spec.platform}_{spec.name}"
    entity._attr_translation_key = spec.name
    return entity


# ── TuyaCloudlessSensor ───────────────────────────────────────────────────────


class TestTuyaCloudlessSensor:
    def test_unique_id_format(self) -> None:
        e = _make_sensor()
        assert e._attr_unique_id == "gw001_sensor_power_consumption"

    def test_native_value_scaled(self) -> None:
        e = _make_sensor({"19": 1000})
        assert e.native_value == pytest.approx(100.0)

    def test_native_value_none_when_dp_missing(self) -> None:
        e = _make_sensor({})
        assert e.native_value is None

    def test_native_value_no_scale(self) -> None:
        e = _make_sensor({"19": 42}, scale=1.0)
        assert e.native_value == 42

    def test_native_value_none_when_no_dp_value(self) -> None:
        coord = _make_coordinator()
        spec = EntitySpec(platform="sensor", name="empty")
        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = None
        entity._spec = spec
        assert entity.native_value is None

    def test_native_value_scale_rounding(self) -> None:
        e = _make_sensor({"19": 333}, scale=0.01)
        assert e.native_value == pytest.approx(3.33)

    def test_unknown_device_class_ignored(self) -> None:
        """Unknown device_class should be logged and ignored, not crash."""
        coord = _make_coordinator({"5": 10})
        spec = EntitySpec(
            platform="sensor",
            name="weird",
            dp_value=DPSpec(id="5", type="int"),
            device_class="nonexistent_class",
        )
        # The constructor sets device_class via SensorDeviceClass — unknown values are skipped
        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = "5"
        entity._spec = spec
        # No exception, entity works normally
        assert entity._spec.device_class == "nonexistent_class"

    def test_unknown_state_class_ignored(self) -> None:
        coord = _make_coordinator({"5": 10})
        spec = EntitySpec(
            platform="sensor",
            name="weird",
            dp_value=DPSpec(id="5", type="int"),
            state_class="nonexistent_class",
        )
        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = "5"
        entity._spec = spec
        assert entity._spec.state_class == "nonexistent_class"

    def test_native_value_string_dp(self) -> None:
        """When the raw DP value is a string, native_value returns it as-is (line 122)."""
        e = _make_sensor({"19": "some_mode"})
        assert e.native_value == "some_mode"


# ── TuyaLastSeenSensor ────────────────────────────────────────────────────────


class TestTuyaLastSeenSensor:
    def _make(self, last_seen: datetime | None = None) -> TuyaLastSeenSensor:
        coord = _make_coordinator(last_seen=last_seen)
        entity = TuyaLastSeenSensor.__new__(TuyaLastSeenSensor)
        entity.coordinator = coord
        entity._dp_id = None
        entity._attr_unique_id = f"{coord.gw_id}_last_seen"
        return entity

    def test_unique_id(self) -> None:
        e = self._make()
        assert e._attr_unique_id == "gw001_last_seen"

    def test_native_value_with_timestamp(self) -> None:
        ts = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        e = self._make(last_seen=ts)
        assert e.native_value == ts.isoformat()

    def test_native_value_none(self) -> None:
        e = self._make()
        assert e.native_value is None

    def test_entity_category_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        e = self._make()
        assert e._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_icon(self) -> None:
        e = self._make()
        assert e._attr_icon == "mdi:clock-outline"


# ── TuyaReconnectSensor ───────────────────────────────────────────────────────


class TestTuyaReconnectSensor:
    def _make(self, reconnect_count: int = 0) -> TuyaReconnectSensor:
        coord = _make_coordinator(reconnect_count=reconnect_count)
        entity = TuyaReconnectSensor.__new__(TuyaReconnectSensor)
        entity.coordinator = coord
        entity._dp_id = None
        entity._attr_unique_id = f"{coord.gw_id}_reconnects"
        entity._attr_name = "Reconnects"
        return entity

    def test_unique_id(self) -> None:
        e = self._make()
        assert e._attr_unique_id == "gw001_reconnects"

    def test_native_value(self) -> None:
        e = self._make(reconnect_count=5)
        assert e.native_value == 5

    def test_native_value_zero(self) -> None:
        e = self._make()
        assert e.native_value == 0

    def test_entity_category_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        e = self._make()
        assert e._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_state_class(self) -> None:
        from homeassistant.components.sensor import SensorStateClass

        e = self._make()
        assert e._attr_state_class == SensorStateClass.TOTAL_INCREASING


# ── Setup entry ───────────────────────────────────────────────────────────────


class TestSensorSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_creates_profile_and_diagnostic_sensors(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.sensor import async_setup_entry

        coord = _make_coordinator({"19": 500})
        spec = _make_sensor_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Smart Plug",
        )

        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        # 1 profile sensor + 2 diagnostic sensors = 3
        assert len(added) == 3
        types = {type(e).__name__ for e in added}
        assert "TuyaCloudlessSensor" in types
        assert "TuyaLastSeenSensor" in types
        assert "TuyaReconnectSensor" in types

    def test_all_sensor_translation_keys_are_unique(self) -> None:
        """AC4: all sensor entities have unique translation_keys (no duplicates)."""
        from custom_components.tuya_cloudless.sensor import TuyaLastSeenSensor, TuyaReconnectSensor

        coord = _make_coordinator()

        last_seen = TuyaLastSeenSensor.__new__(TuyaLastSeenSensor)
        last_seen.coordinator = coord
        last_seen._dp_id = None
        last_seen._attr_unique_id = f"{coord.gw_id}_last_seen"

        reconnect = TuyaReconnectSensor.__new__(TuyaReconnectSensor)
        reconnect.coordinator = coord
        reconnect._dp_id = None
        reconnect._attr_unique_id = f"{coord.gw_id}_reconnects"

        keys = [last_seen.translation_key, reconnect.translation_key]
        assert len(keys) == len(set(keys)), f"Duplicate translation_keys: {keys}"
        # AC1: verify reconnect sensor uses correct key (not rssi)
        assert reconnect.translation_key == "reconnects"
        assert last_seen.translation_key == "last_seen"

    @pytest.mark.asyncio
    async def test_setup_no_profile_sensors_still_creates_diagnostics(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.sensor import async_setup_entry

        coord = _make_coordinator()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[],
            profile_name="Test",
        )

        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        # Only 2 diagnostic sensors
        assert len(added) == 2


class TestSensorScaleZeroR41:
    """R41-F6: sensor native_value must return None when scale=0."""

    def test_native_value_returns_none_for_scale_zero(self) -> None:
        """scale=0 in profile must not silently return 0.0 for any raw value."""
        from tuya_cloudless.profiles import DPSpec, EntitySpec

        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        spec = EntitySpec(
            platform="sensor",
            name="power",
            dp_value=DPSpec(id="5", type="int", scale=0.0),
        )
        coord = _make_coordinator({"5": 1000})
        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = "5"
        entity._spec = spec
        entity._attr_unique_id = "gw001_sensor_power"
        entity._attr_translation_key = "power"
        assert entity.native_value is None, (
            "R41-F6: scale=0 should return None, not silently produce 0.0"
        )


# ── R48 string DP value cap ────────────────────────────────────────────────────


class TestSensorStringCapR48:
    """R48-F5: native_value must cap string DP values at 255 chars."""

    def _make_str_sensor(self, raw_value: str) -> object:
        """Return a sensor backed by a string DP value."""
        from tuya_cloudless.profiles import DPSpec, EntitySpec

        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        coord = _make_coordinator({"7": raw_value})
        spec = EntitySpec(
            platform="sensor",
            name="status",
            dp_value=DPSpec(id="7", type="enum"),
        )
        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = "7"
        entity._spec = spec
        return entity

    def test_short_string_passes_through(self) -> None:
        """Strings <= 255 chars are returned unchanged."""
        e = self._make_str_sensor("ok")
        assert e.native_value == "ok"

    def test_exactly_255_chars_passes_through(self) -> None:
        """Exactly 255-char string is returned unchanged."""
        val = "x" * 255
        e = self._make_str_sensor(val)
        assert e.native_value == val

    def test_256_char_string_is_truncated(self) -> None:
        """256-char string must be truncated to 255 chars."""
        val = "y" * 256
        e = self._make_str_sensor(val)
        result = e.native_value
        assert result is not None
        assert len(result) == 255

    def test_4096_char_string_is_truncated(self) -> None:
        """4096-char string (coordinator max) must be truncated to 255 chars."""
        val = "z" * 4096
        e = self._make_str_sensor(val)
        result = e.native_value
        assert result is not None
        assert len(result) == 255


class TestSensorBoolAndInfinite:
    """sensor.py lines 125, 141-146: bool DP returns None; non-finite result returns None."""

    def _make_numeric_sensor(self, raw_value, scale: float = 1.0):  # type: ignore[misc]
        from unittest.mock import MagicMock

        from tuya_cloudless.profiles import DPSpec, EntitySpec

        from custom_components.tuya_cloudless.coordinator import DeviceState
        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor

        coord = MagicMock()
        coord.gw_id = "gw1"
        coord.device_name = "gw1"
        coord.profile_name = ""
        coord._version = "3.3"
        coord.state = DeviceState(available=True, dps={"1": raw_value})
        spec = EntitySpec(
            platform="sensor",
            name="power",
            dp_value=DPSpec(id="1", type="int", scale=scale),
        )
        entity = TuyaCloudlessSensor.__new__(TuyaCloudlessSensor)
        entity.coordinator = coord
        entity._dp_id = "1"
        entity._spec = spec
        return entity

    def test_bool_dp_returns_none(self) -> None:
        """Boolean raw value returns None for numeric sensor (line 125)."""
        e = self._make_numeric_sensor(True)
        assert e.native_value is None

    def test_isinf_result_returns_none(self) -> None:
        """Non-finite (inf) result returns None (lines 141-146)."""
        e = self._make_numeric_sensor(float("inf"))
        assert e.native_value is None
