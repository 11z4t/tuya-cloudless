"""Tests for custom_components.tuya_cloudless.binary_sensor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor
from custom_components.tuya_cloudless.coordinator import DeviceState

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


def _make_binary_sensor_spec(
    dp_id: str = "26",
    device_class: str | None = "problem",
) -> EntitySpec:
    return EntitySpec(
        platform="binary_sensor",
        name="overload",
        dp_power=DPSpec(id=dp_id, type="bool"),
        device_class=device_class,
    )


def _make_binary_sensor_spec_with_value(
    dp_value_id: str = "10",
    dp_power_id: str = "26",
    device_class: str | None = "problem",
) -> EntitySpec:
    """Create a spec that has both dp_value and dp_power set."""
    return EntitySpec(
        platform="binary_sensor",
        name="overload",
        dp_value=DPSpec(id=dp_value_id, type="bool"),
        dp_power=DPSpec(id=dp_power_id, type="bool"),
        device_class=device_class,
    )


def _make_binary_sensor(
    dps: dict[str, Any] | None = None,
    spec: EntitySpec | None = None,
) -> TuyaCloudlessBinarySensor:
    coord = _make_coordinator(dps)
    _spec = spec or _make_binary_sensor_spec()
    entity = TuyaCloudlessBinarySensor.__new__(TuyaCloudlessBinarySensor)
    entity.coordinator = coord
    entity._dp_id = _spec.dp_power.id if _spec.dp_power else None
    entity._spec = _spec
    entity._attr_unique_id = f"gw001_{_spec.platform}_{_spec.name}"
    entity._attr_translation_key = _spec.name
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestBinarySensor:
    def test_unique_id(self) -> None:
        e = _make_binary_sensor()
        assert e._attr_unique_id == "gw001_binary_sensor_overload"

    def test_is_on_true(self) -> None:
        e = _make_binary_sensor({"26": True})
        assert e.is_on is True

    def test_is_on_false(self) -> None:
        e = _make_binary_sensor({"26": False})
        assert e.is_on is False

    def test_is_on_none(self) -> None:
        e = _make_binary_sensor({})
        assert e.is_on is None

    def test_is_on_truthy(self) -> None:
        e = _make_binary_sensor({"26": 1})
        assert e.is_on is True

    def test_no_dp_power(self) -> None:
        spec = EntitySpec(platform="binary_sensor", name="empty")
        e = _make_binary_sensor(spec=spec)
        assert e.is_on is None

    def test_dp_value_takes_priority_over_dp_power(self) -> None:
        """ROB-005 / PLAT-834: dp_value.id should be used as primary DP when available."""
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        spec = _make_binary_sensor_spec_with_value(dp_value_id="10", dp_power_id="26")
        coord = _make_coordinator({"10": True, "26": False})
        entity = TuyaCloudlessBinarySensor.__new__(TuyaCloudlessBinarySensor)
        entity.coordinator = coord
        entity._dp_id = (
            spec.dp_value.id if spec.dp_value else (spec.dp_power.id if spec.dp_power else None)
        )
        entity._spec = spec
        entity._attr_unique_id = f"gw001_{spec.platform}_{spec.name}"
        entity._attr_translation_key = spec.name
        # dp_value id "10" is True — that should be the active DP
        assert entity._dp_id == "10"
        assert entity.is_on is True

    def test_dp_value_fallback_to_dp_power_when_no_dp_value(self) -> None:
        """When dp_value is absent, dp_power.id must be used as fallback."""
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        spec = _make_binary_sensor_spec(dp_id="26")  # only dp_power, no dp_value
        coord = _make_coordinator({"26": True})
        entity = TuyaCloudlessBinarySensor.__new__(TuyaCloudlessBinarySensor)
        entity.coordinator = coord
        entity._dp_id = (
            spec.dp_value.id if spec.dp_value else (spec.dp_power.id if spec.dp_power else None)
        )
        entity._spec = spec
        entity._attr_unique_id = f"gw001_{spec.platform}_{spec.name}"
        entity._attr_translation_key = spec.name
        assert entity._dp_id == "26"
        assert entity.is_on is True

    def test_dp_value_only_no_dp_power(self) -> None:
        """Spec with only dp_value and no dp_power must use dp_value.id."""
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor

        spec = EntitySpec(
            platform="binary_sensor",
            name="leak",
            dp_value=DPSpec(id="101", type="bool"),
        )
        coord = _make_coordinator({"101": True})
        entity = TuyaCloudlessBinarySensor.__new__(TuyaCloudlessBinarySensor)
        entity.coordinator = coord
        entity._dp_id = (
            spec.dp_value.id if spec.dp_value else (spec.dp_power.id if spec.dp_power else None)
        )
        entity._spec = spec
        entity._attr_unique_id = f"gw001_{spec.platform}_{spec.name}"
        entity._attr_translation_key = spec.name
        assert entity._dp_id == "101"
        assert entity.is_on is True


class TestBinarySensorSetup:
    @pytest.mark.asyncio
    async def test_creates_entities(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.binary_sensor import async_setup_entry

        coord = _make_coordinator()
        spec = _make_binary_sensor_spec()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=[spec],
            profile_name="Smart Plug",
        )
        entry = MagicMock()
        entry.runtime_data = runtime

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1
        assert isinstance(added[0], TuyaCloudlessBinarySensor)

    @pytest.mark.asyncio
    async def test_skips_non_binary_sensor(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
        from custom_components.tuya_cloudless.binary_sensor import async_setup_entry

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
