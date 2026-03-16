"""Tests for platform async_setup_entry functions (all platforms)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData
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


def _make_runtime(specs: list[EntitySpec]) -> TuyaCloudlessRuntimeData:
    return TuyaCloudlessRuntimeData(
        coordinator=_make_coordinator(),
        entity_specs=specs,
        profile_name="Test",
    )


def _make_entry(runtime: TuyaCloudlessRuntimeData) -> MagicMock:
    entry = MagicMock()
    entry.runtime_data = runtime
    return entry


# ── Switch ─────────────────────────────────────────────────────────────────────


class TestSwitchSetup:
    @pytest.mark.asyncio
    async def test_creates_switch_entities(self) -> None:
        from custom_components.tuya_cloudless.switch import async_setup_entry

        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_skips_non_switch_specs(self) -> None:
        from custom_components.tuya_cloudless.switch import async_setup_entry

        spec = EntitySpec(platform="sensor", name="power")
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 0


# ── Light ──────────────────────────────────────────────────────────────────────


class TestLightSetup:
    @pytest.mark.asyncio
    async def test_creates_light_entities(self) -> None:
        from custom_components.tuya_cloudless.light import async_setup_entry

        spec = EntitySpec(
            platform="light",
            name="light",
            dp_power=DPSpec(id="1", type="bool"),
            dp_brightness=DPSpec(id="2", type="int", min_raw=10, max_raw=1000),
        )
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_skips_non_light_specs(self) -> None:
        from custom_components.tuya_cloudless.light import async_setup_entry

        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 0


# ── Sensor ─────────────────────────────────────────────────────────────────────


class TestSensorSetup:
    @pytest.mark.asyncio
    async def test_creates_sensor_entities(self) -> None:
        from custom_components.tuya_cloudless.sensor import async_setup_entry

        spec = EntitySpec(
            platform="sensor",
            name="power",
            dp_value=DPSpec(id="19", type="int", scale=0.1),
            unit="W",
            device_class="power",
            state_class="measurement",
        )
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        # 1 profile sensor + 2 diagnostic sensors = 3
        assert len(added) == 3

    @pytest.mark.asyncio
    async def test_diagnostic_sensors_always_present(self) -> None:
        from custom_components.tuya_cloudless.sensor import async_setup_entry

        runtime = _make_runtime([])  # No profile specs
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 2  # Only diagnostics


# ── Binary Sensor ──────────────────────────────────────────────────────────────


class TestBinarySensorSetup:
    @pytest.mark.asyncio
    async def test_creates_binary_sensor_entities(self) -> None:
        from custom_components.tuya_cloudless.binary_sensor import async_setup_entry

        spec = EntitySpec(
            platform="binary_sensor",
            name="overload",
            dp_power=DPSpec(id="26", type="bool"),
            device_class="problem",
        )
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_skips_non_binary_sensor_specs(self) -> None:
        from custom_components.tuya_cloudless.binary_sensor import async_setup_entry

        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 0


# ── Cover ──────────────────────────────────────────────────────────────────────


class TestCoverSetup:
    @pytest.mark.asyncio
    async def test_creates_cover_entities(self) -> None:
        from custom_components.tuya_cloudless.cover import async_setup_entry

        spec = EntitySpec(
            platform="cover",
            name="blind",
            dp_open=DPSpec(id="1", type="bool"),
            dp_position=DPSpec(id="2", type="int"),
            device_class="blind",
        )
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_skips_non_cover_specs(self) -> None:
        from custom_components.tuya_cloudless.cover import async_setup_entry

        spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        runtime = _make_runtime([spec])
        entry = _make_entry(runtime)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == 0


# ── Multi-platform ─────────────────────────────────────────────────────────────


class TestMultiPlatformSetup:
    @pytest.mark.asyncio
    async def test_mixed_specs_routed_correctly(self) -> None:
        """Ensure specs are only picked up by their matching platform."""
        from custom_components.tuya_cloudless.light import (
            async_setup_entry as light_setup,
        )
        from custom_components.tuya_cloudless.switch import (
            async_setup_entry as switch_setup,
        )

        switch_spec = EntitySpec(platform="switch", name="sw", dp_power=DPSpec(id="1", type="bool"))
        light_spec = EntitySpec(
            platform="light",
            name="light",
            dp_power=DPSpec(id="1", type="bool"),
            dp_brightness=DPSpec(id="2", type="int", min_raw=10, max_raw=1000),
        )
        runtime = _make_runtime([switch_spec, light_spec])
        entry = _make_entry(runtime)

        switch_added: list = []
        await switch_setup(MagicMock(), entry, lambda e: switch_added.extend(e))
        assert len(switch_added) == 1

        light_added: list = []
        await light_setup(MagicMock(), entry, lambda e: light_added.extend(e))
        assert len(light_added) == 1
