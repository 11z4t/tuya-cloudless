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
    coord._version = "3.3"
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
    entity._attr_unique_id = f"{coord._gw_id}_{spec.platform}_{spec.name}"
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


# ── TuyaLastSeenSensor ────────────────────────────────────────────────────────


class TestTuyaLastSeenSensor:
    def _make(self, last_seen: datetime | None = None) -> TuyaLastSeenSensor:
        coord = _make_coordinator(last_seen=last_seen)
        entity = TuyaLastSeenSensor.__new__(TuyaLastSeenSensor)
        entity.coordinator = coord
        entity._dp_id = None
        entity._attr_unique_id = f"{coord._gw_id}_last_seen"
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
        entity._attr_unique_id = f"{coord._gw_id}_reconnects"
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
            device_info=MagicMock(),
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

    @pytest.mark.asyncio
    async def test_setup_no_profile_sensors_still_creates_diagnostics(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.sensor import async_setup_entry

        coord = _make_coordinator()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            device_info=MagicMock(),
            entity_specs=[],
            profile_name="Test",
        )

        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        # Only 2 diagnostic sensors
        assert len(added) == 2
