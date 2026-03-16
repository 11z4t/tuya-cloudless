"""Tests for PLAT-766 — zeroconf and DHCP auto-discovery config flow steps."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
from custom_components.tuya_cloudless.const import (
    CONF_GW_ID,
    CONF_IP_ADDRESS,
    CONF_PROTOCOL_VERSION,
    DEFAULT_PROTOCOL_VERSION,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_flow() -> TuyaCloudlessConfigFlow:
    flow = TuyaCloudlessConfigFlow()
    flow.hass = MagicMock()
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_abort = MagicMock(return_value={"type": "abort"})
    flow.context = {}
    return flow


def _make_zeroconf_info(
    host: str = "192.168.1.100",
    name: str = "abc123._tuya._tcp.local.",
    properties: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a mock ZeroconfServiceInfo with the given attributes."""
    info = MagicMock()
    info.host = host
    info.name = name
    info.properties = properties if properties is not None else {"gwId": "abc123", "version": "3.3"}
    return info


def _make_dhcp_info(
    ip: str = "192.168.1.100",
    macaddress: str = "d8:96:e0:11:22:33",
    hostname: str = "tuya-device",
) -> MagicMock:
    """Return a mock DhcpServiceInfo with the given attributes."""
    info = MagicMock()
    info.ip = ip
    info.macaddress = macaddress
    info.hostname = hostname
    return info


_DISCOVERED_DEVICE: dict[str, Any] = {
    CONF_GW_ID: "abc123",
    CONF_IP_ADDRESS: "192.168.1.100",
    CONF_PROTOCOL_VERSION: "3.3",
    "product_key": "pk001",
    "encrypt": True,
}


# ── async_step_zeroconf ───────────────────────────────────────────────────────


class TestStepZeroconf:
    @pytest.mark.asyncio
    async def test_gwid_from_txt_properties(self) -> None:
        """gw_id in TXT gwId field → sets unique ID and proceeds to discovery."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_zeroconf_info(properties={"gwId": "abc123", "version": "3.4"})

        await flow.async_step_zeroconf(info)

        flow.async_set_unique_id.assert_called_once_with("abc123")
        flow._abort_if_unique_id_configured.assert_called_once()
        assert flow._device[CONF_GW_ID] == "abc123"
        assert flow._device[CONF_IP_ADDRESS] == "192.168.1.100"
        assert flow._device[CONF_PROTOCOL_VERSION] == "3.4"
        flow.async_step_discovery.assert_called_once()

    @pytest.mark.asyncio
    async def test_gwid_from_deviceid_property(self) -> None:
        """Falls back to deviceId TXT field when gwId is absent."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_zeroconf_info(properties={"deviceId": "dev999"})

        await flow.async_step_zeroconf(info)

        flow.async_set_unique_id.assert_called_once_with("dev999")
        assert flow._device[CONF_GW_ID] == "dev999"

    @pytest.mark.asyncio
    async def test_gwid_from_service_name_fallback(self) -> None:
        """Uses first label of service name when TXT records are empty."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_zeroconf_info(name="devXYZ789._tuya._tcp.local.", properties={})

        await flow.async_step_zeroconf(info)

        flow.async_set_unique_id.assert_called_once_with("devXYZ789")
        assert flow._device[CONF_GW_ID] == "devXYZ789"

    @pytest.mark.asyncio
    async def test_no_device_id_aborts(self) -> None:
        """Aborts with no_device_id when gw_id cannot be determined."""
        flow = _make_flow()
        info = _make_zeroconf_info(name="", properties={})

        result = await flow.async_step_zeroconf(info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")
        assert result == {"type": "abort"}

    @pytest.mark.asyncio
    async def test_already_configured_updates_ip(self) -> None:
        """_abort_if_unique_id_configured is called with the new IP address."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_zeroconf_info(host="10.0.0.5", properties={"gwId": "abc123"})

        await flow.async_step_zeroconf(info)

        flow._abort_if_unique_id_configured.assert_called_once_with(
            updates={CONF_IP_ADDRESS: "10.0.0.5"}
        )

    @pytest.mark.asyncio
    async def test_default_protocol_version_when_missing(self) -> None:
        """Uses DEFAULT_PROTOCOL_VERSION when version is absent from TXT records."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_zeroconf_info(properties={"gwId": "abc123"})

        await flow.async_step_zeroconf(info)

        assert flow._device[CONF_PROTOCOL_VERSION] == DEFAULT_PROTOCOL_VERSION

    @pytest.mark.asyncio
    async def test_product_key_from_txt(self) -> None:
        """Extracts productKey TXT field for profile auto-suggestion."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_zeroconf_info(
            properties={"gwId": "abc123", "productKey": "pkey42"}
        )

        await flow.async_step_zeroconf(info)

        assert flow._device.get("product_key") == "pkey42"

    @pytest.mark.asyncio
    async def test_title_placeholder_set(self) -> None:
        """Sets context title_placeholders so HA shows the device ID in UI."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_zeroconf_info(properties={"gwId": "mydev"})

        await flow.async_step_zeroconf(info)

        assert flow.context.get("title_placeholders", {}).get("name") == "mydev"


# ── async_step_dhcp ───────────────────────────────────────────────────────────


class TestStepDhcp:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        """Device found in UDP scan → sets unique ID and proceeds to discovery."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_dhcp_info(ip="192.168.1.100")

        with patch.object(
            flow, "_run_discovery", AsyncMock(return_value=[_DISCOVERED_DEVICE])
        ):
            await flow.async_step_dhcp(info)

        flow.async_set_unique_id.assert_called_once_with("abc123")
        flow._abort_if_unique_id_configured.assert_called_once()
        assert flow._device[CONF_GW_ID] == "abc123"
        assert flow._device[CONF_IP_ADDRESS] == "192.168.1.100"
        flow.async_step_discovery.assert_called_once()

    @pytest.mark.asyncio
    async def test_device_not_in_scan_aborts(self) -> None:
        """Aborts when UDP scan has no match for the DHCP-reported IP."""
        flow = _make_flow()
        info = _make_dhcp_info(ip="192.168.1.200")  # different IP

        with patch.object(
            flow, "_run_discovery", AsyncMock(return_value=[_DISCOVERED_DEVICE])
        ):
            result = await flow.async_step_dhcp(info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")
        assert result == {"type": "abort"}

    @pytest.mark.asyncio
    async def test_empty_scan_aborts(self) -> None:
        """Aborts when UDP scan returns an empty list."""
        flow = _make_flow()
        info = _make_dhcp_info()

        with patch.object(flow, "_run_discovery", AsyncMock(return_value=[])):
            result = await flow.async_step_dhcp(info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_oserror_in_scan_aborts(self) -> None:
        """Aborts with no_device_id when UDP scan raises OSError."""
        flow = _make_flow()
        info = _make_dhcp_info()

        with patch.object(
            flow, "_run_discovery", AsyncMock(side_effect=OSError("network error"))
        ):
            result = await flow.async_step_dhcp(info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_timeout_in_scan_aborts(self) -> None:
        """Aborts with no_device_id when UDP scan times out."""
        flow = _make_flow()
        info = _make_dhcp_info()

        with patch.object(
            flow, "_run_discovery", AsyncMock(side_effect=TimeoutError())
        ):
            result = await flow.async_step_dhcp(info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_already_configured_updates_ip(self) -> None:
        """_abort_if_unique_id_configured is called with the discovered IP."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_dhcp_info(ip="192.168.1.100")

        with patch.object(
            flow, "_run_discovery", AsyncMock(return_value=[_DISCOVERED_DEVICE])
        ):
            await flow.async_step_dhcp(info)

        flow._abort_if_unique_id_configured.assert_called_once_with(
            updates={CONF_IP_ADDRESS: "192.168.1.100"}
        )

    @pytest.mark.asyncio
    async def test_title_placeholder_set(self) -> None:
        """Sets context title_placeholders so HA shows the device ID in UI."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_dhcp_info(ip="192.168.1.100")

        with patch.object(
            flow, "_run_discovery", AsyncMock(return_value=[_DISCOVERED_DEVICE])
        ):
            await flow.async_step_dhcp(info)

        assert flow.context.get("title_placeholders", {}).get("name") == "abc123"

    @pytest.mark.asyncio
    async def test_multiple_devices_picks_matching_ip(self) -> None:
        """Picks the device whose IP matches DHCP info when multiple devices found."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        info = _make_dhcp_info(ip="192.168.1.101")

        other_device: dict[str, Any] = {
            CONF_GW_ID: "other99",
            CONF_IP_ADDRESS: "192.168.1.101",
            CONF_PROTOCOL_VERSION: "3.4",
            "product_key": "pk002",
            "encrypt": False,
        }

        with patch.object(
            flow,
            "_run_discovery",
            AsyncMock(return_value=[_DISCOVERED_DEVICE, other_device]),
        ):
            await flow.async_step_dhcp(info)

        flow.async_set_unique_id.assert_called_once_with("other99")
        assert flow._device[CONF_GW_ID] == "other99"
