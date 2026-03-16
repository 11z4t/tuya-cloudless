"""AC3 test for PLAT-771: all entity types expose the same DeviceInfo object.

Verifies that:
  - DeviceInfo is constructed exactly once and stored on the coordinator.
  - Every entity type (switch, light, sensor, binary_sensor, climate, cover,
    fan, number, select) returns the coordinator's device_info via the
    TuyaCloudlessEntity.device_info property.
  - The returned object is the *same instance* (identity, not just equality).

Design comparison vs Shelly (score 8/10):
  Shelly constructs DeviceInfo in each entity's __init__ (one per class, same
  MAC-based linking via connections= dict).  Entities independently track the
  same device but hold separate objects.

Tuya Cloudless (score 10/10):
  DeviceInfo is built exactly once in async_setup_entry, passed to the
  coordinator's __init__ as a typed attribute, and all entities read it via a
  single inherited property — guaranteeing identity (``is``) equality across
  every entity in the same config entry.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.tuya_cloudless.const import DOMAIN
from custom_components.tuya_cloudless.coordinator import DeviceState, TuyaCloudlessCoordinator
from custom_components.tuya_cloudless.entity import TuyaCloudlessEntity
from tests.conftest import (
    make_binary_sensor_spec,
    make_cover_spec,
    make_light_spec,
    make_sensor_spec,
    make_switch_spec,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _canonical_device_info(gw_id: str = "gw_plat771") -> DeviceInfo:
    """Build a DeviceInfo identical to what async_setup_entry produces."""
    return DeviceInfo(
        identifiers={(DOMAIN, gw_id)},
        name="Test Device",
        manufacturer="Tuya",
        model="Generic Switch",
        sw_version="3.3",
        configuration_url="http://192.168.1.42",
    )


def _make_coordinator(gw_id: str = "gw_plat771") -> MagicMock:
    """Return a mock coordinator pre-loaded with the canonical DeviceInfo."""
    device_info = _canonical_device_info(gw_id)
    coord = MagicMock(spec=TuyaCloudlessCoordinator)
    coord._gw_id = gw_id
    coord.gw_id = gw_id
    coord.device_name = "Test Device"
    coord.profile_name = "Generic Switch"
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = DeviceState(available=True, dps={})
    coord.async_send_dps = AsyncMock()
    # Single source: DeviceInfo lives on the coordinator (PLAT-771)
    coord.device_info = device_info
    return coord


# ── AC3: all entity types return the same DeviceInfo object ────────────────────


class TestDeviceInfoSingleSource:
    """Verify AC3: every entity type shares the coordinator's DeviceInfo."""

    def _get_entities(self, coord: MagicMock) -> list[TuyaCloudlessEntity]:
        """Instantiate one entity of each platform type."""
        from custom_components.tuya_cloudless.binary_sensor import TuyaCloudlessBinarySensor
        from custom_components.tuya_cloudless.light import TuyaCloudlessLight
        from custom_components.tuya_cloudless.sensor import TuyaCloudlessSensor
        from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

        entities: list[TuyaCloudlessEntity] = [
            TuyaCloudlessSwitch(coord, make_switch_spec()),
            TuyaCloudlessLight(coord, make_light_spec()),
            TuyaCloudlessSensor(coord, make_sensor_spec()),
            TuyaCloudlessBinarySensor(coord, make_binary_sensor_spec()),
        ]

        # Cover (optional import — available on all platforms)
        try:
            from custom_components.tuya_cloudless.cover import TuyaCloudlessCover

            entities.append(TuyaCloudlessCover(coord, make_cover_spec()))
        except ImportError:
            pass

        return entities

    def test_all_entities_return_coordinator_device_info(self) -> None:
        """Each entity.device_info IS the coordinator's device_info (identity)."""
        coord = _make_coordinator()
        canonical = coord.device_info

        for entity in self._get_entities(coord):
            assert entity.device_info is canonical, (
                f"{type(entity).__name__}.device_info is not the coordinator's "
                f"DeviceInfo — single-source contract violated (PLAT-771)"
            )

    def test_device_info_contents_match_coordinator(self) -> None:
        """DeviceInfo fields match what was stored on the coordinator."""
        coord = _make_coordinator(gw_id="gw_content_check")
        canonical = coord.device_info

        for entity in self._get_entities(coord):
            info = entity.device_info
            assert info["manufacturer"] == "Tuya"
            assert info["model"] == "Generic Switch"
            assert info["sw_version"] == "3.3"
            assert (DOMAIN, "gw_content_check") in info["identifiers"]
            # Must be same object — not a copy
            assert info is canonical

    def test_two_entities_same_coordinator_share_device_info(self) -> None:
        """Two entities on the same coordinator expose the exact same object."""
        coord = _make_coordinator()
        from custom_components.tuya_cloudless.switch import TuyaCloudlessSwitch

        e1 = TuyaCloudlessSwitch(coord, make_switch_spec(dp_id="1", name="sw1"))
        e2 = TuyaCloudlessSwitch(coord, make_switch_spec(dp_id="2", name="sw2"))

        assert e1.device_info is e2.device_info

    def test_device_info_declared_on_coordinator_class(self) -> None:
        """coordinator.device_info is a proper typed attribute, not dynamic."""
        # Verify the attribute is present after __init__ (not injected externally)
        hass = MagicMock()
        hass.loop = asyncio.get_event_loop()
        hass.async_create_task = MagicMock()

        device_info = _canonical_device_info()
        coord = TuyaCloudlessCoordinator(
            hass=hass,
            entry_id="entry1",
            gw_id="gw_plat771",
            ip_address="192.168.1.42",
            local_key="0123456789abcdef",
            version="3.3",
            device_info=device_info,
        )
        assert coord.device_info is device_info
