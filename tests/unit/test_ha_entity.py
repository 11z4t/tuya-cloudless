"""Tests for custom_components.tuya_cloudless.entity."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.tuya_cloudless.const import DOMAIN
from custom_components.tuya_cloudless.coordinator import DeviceState, TuyaCloudlessCoordinator
from custom_components.tuya_cloudless.entity import TuyaCloudlessEntity


def _make_coordinator(
    *,
    dps: dict[str, Any] | None = None,
    available: bool = True,
    gw_id: str = "abc123",
    version: str = "3.3",
    device_name: str = "",
    profile_name: str = "",
) -> MagicMock:
    """Create a mock coordinator with a pre-built DeviceInfo (mirrors async_setup_entry)."""
    coord = MagicMock(spec=TuyaCloudlessCoordinator)
    coord._gw_id = gw_id
    coord.gw_id = gw_id
    coord._version = version
    coord.version = version
    resolved_name = device_name or gw_id
    coord.device_name = resolved_name
    coord.profile_name = profile_name
    coord.state = DeviceState(
        available=available,
        dps=dps or {},
    )
    coord.async_send_dps = AsyncMock()
    # Build DeviceInfo matching what async_setup_entry produces (PLAT-714)
    coord.device_info = DeviceInfo(
        identifiers={(DOMAIN, gw_id)},
        name=resolved_name,
        manufacturer="Tuya",
        model=profile_name or "Tuya Cloudless",
        sw_version=version,
        configuration_url="http://",
    )
    return coord


class TestTuyaCloudlessEntity:
    def test_available_true(self) -> None:
        coord = _make_coordinator(available=True)
        entity = TuyaCloudlessEntity(coord, dp_id="1")
        assert entity.available is True

    def test_available_false(self) -> None:
        coord = _make_coordinator(available=False)
        entity = TuyaCloudlessEntity(coord, dp_id="1")
        assert entity.available is False

    def test_device_info(self) -> None:
        coord = _make_coordinator(gw_id="mydevice", profile_name="Smart Thermostat")
        entity = TuyaCloudlessEntity(coord)
        info = entity.device_info
        assert (DOMAIN, "mydevice") in info["identifiers"]
        assert info["manufacturer"] == "Tuya"
        assert info["model"] == "Smart Thermostat"
        assert info["sw_version"] == "3.3"

    def test_get_dp_with_id(self) -> None:
        coord = _make_coordinator(dps={"1": True, "2": 50})
        entity = TuyaCloudlessEntity(coord, dp_id="1")
        assert entity.get_dp() is True
        assert entity.get_dp("2") == 50

    def test_get_dp_missing(self) -> None:
        coord = _make_coordinator(dps={})
        entity = TuyaCloudlessEntity(coord, dp_id="1")
        assert entity.get_dp() is None

    def test_get_dp_no_dp_id(self) -> None:
        coord = _make_coordinator(dps={"1": True})
        entity = TuyaCloudlessEntity(coord)
        assert entity.get_dp() is None

    @pytest.mark.asyncio
    async def test_async_send_dp(self) -> None:
        coord = _make_coordinator()
        entity = TuyaCloudlessEntity(coord, dp_id="1")
        await entity.async_send_dp("1", True)
        coord.async_send_dps.assert_awaited_once_with({"1": True})

    def test_has_entity_name(self) -> None:
        coord = _make_coordinator()
        entity = TuyaCloudlessEntity(coord)
        assert entity._attr_has_entity_name is True

    def test_extra_state_attributes_with_dp(self) -> None:
        coord = _make_coordinator(dps={"1": True})
        entity = TuyaCloudlessEntity(coord, dp_id="1")
        attrs = entity.extra_state_attributes
        assert attrs["dp_id"] == "1"
        assert attrs["raw_value"] is True

    def test_extra_state_attributes_empty_when_no_dp_id(self) -> None:
        coord = _make_coordinator(dps={"1": True})
        entity = TuyaCloudlessEntity(coord)
        assert entity.extra_state_attributes == {}

    def test_extra_state_attributes_raw_value_none(self) -> None:
        coord = _make_coordinator(dps={})
        entity = TuyaCloudlessEntity(coord, dp_id="99")
        attrs = entity.extra_state_attributes
        assert attrs["dp_id"] == "99"
        assert attrs["raw_value"] is None

    def test_device_info_name(self) -> None:
        coord = _make_coordinator(gw_id="gw_test", device_name="My Thermostat")
        entity = TuyaCloudlessEntity(coord)
        info = entity.device_info
        assert info["name"] == "My Thermostat"

    def test_device_info_model_falls_back_to_tuya_cloudless(self) -> None:
        coord = _make_coordinator(gw_id="gw_test", profile_name="")
        entity = TuyaCloudlessEntity(coord)
        info = entity.device_info
        assert info["model"] == "Tuya Cloudless"
