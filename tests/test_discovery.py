"""Tests for Tuya device discovery."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.tuya_cloudless.discovery import (
    DISCOVERY_PORT,
    TuyaDevice,
    TuyaDiscovery,
    TuyaDiscoveryProtocol,
    discover_devices,
)


class TestTuyaDevice:
    """Test TuyaDevice class."""

    def test_init_complete_data(self) -> None:
        """Test initialization with complete data."""
        data = {
            "gwId": "device123",
            "devId": "device123",
            "productKey": "key123",
            "version": "3.3",
            "encrypted": True,
        }
        device = TuyaDevice(data, "192.168.1.100")

        assert device.ip == "192.168.1.100"
        assert device.device_id == "device123"
        assert device.product_key == "key123"
        assert device.protocol_version == "3.3"
        assert device.encrypted is True

    def test_init_minimal_data(self) -> None:
        """Test initialization with minimal data."""
        data = {}
        device = TuyaDevice(data, "192.168.1.100")

        assert device.ip == "192.168.1.100"
        assert device.device_id == ""
        assert device.product_key == ""
        assert device.protocol_version == "3.3"
        assert device.encrypted is False

    def test_init_devid_fallback(self) -> None:
        """Test devId fallback when gwId is missing."""
        data = {"devId": "device456"}
        device = TuyaDevice(data, "192.168.1.101")

        assert device.device_id == "device456"

    def test_repr(self) -> None:
        """Test string representation."""
        data = {"gwId": "test", "version": "3.4"}
        device = TuyaDevice(data, "192.168.1.100")

        repr_str = repr(device)
        assert "192.168.1.100" in repr_str
        assert "test" in repr_str
        assert "3.4" in repr_str

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        data = {
            "gwId": "device123",
            "productKey": "key123",
            "version": "3.3",
        }
        device = TuyaDevice(data, "192.168.1.100")

        result = device.to_dict()

        assert result["ip"] == "192.168.1.100"
        assert result["device_id"] == "device123"
        assert result["product_key"] == "key123"
        assert result["protocol_version"] == "3.3"
        assert "raw_data" in result


class TestTuyaDiscovery:
    """Test TuyaDiscovery class."""

    def test_init_default_values(self) -> None:
        """Test initialization with default values."""
        discovery = TuyaDiscovery()

        assert discovery.broadcast_address == "255.255.255.255"
        assert discovery.port == DISCOVERY_PORT
        assert discovery.timeout == 3.0

    def test_init_custom_values(self) -> None:
        """Test initialization with custom values."""
        discovery = TuyaDiscovery(
            broadcast_address="192.168.1.255",
            port=7777,
            timeout=5.0,
        )

        assert discovery.broadcast_address == "192.168.1.255"
        assert discovery.port == 7777
        assert discovery.timeout == 5.0

    @pytest.mark.asyncio
    async def test_discover_success(self) -> None:
        """Test successful device discovery."""
        discovery = TuyaDiscovery(timeout=0.1)

        # Mock the datagram endpoint
        mock_transport = MagicMock()
        mock_protocol = MagicMock()

        async def mock_create_endpoint(protocol_factory, **kwargs):
            return mock_transport, mock_protocol

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_event_loop = AsyncMock()
            mock_event_loop.create_datagram_endpoint = mock_create_endpoint
            mock_loop.return_value = mock_event_loop

            devices = await discovery.discover()

            assert isinstance(devices, list)
            mock_transport.sendto.assert_called_once()
            mock_transport.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_with_local_key(self) -> None:
        """Test discovery with local key."""
        discovery = TuyaDiscovery(timeout=0.1)

        mock_transport = MagicMock()
        mock_protocol = MagicMock()

        async def mock_create_endpoint(protocol_factory, **kwargs):
            return mock_transport, mock_protocol

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_event_loop = AsyncMock()
            mock_event_loop.create_datagram_endpoint = mock_create_endpoint
            mock_loop.return_value = mock_event_loop

            devices = await discovery.discover(local_key="1234567890abcdef")

            assert isinstance(devices, list)

    def test_add_device_new(self) -> None:
        """Test adding a new device."""
        discovery = TuyaDiscovery()
        device = TuyaDevice({"gwId": "dev1"}, "192.168.1.100")

        discovery._add_device(device)

        assert len(discovery._discovered_devices) == 1
        assert "192.168.1.100:dev1" in discovery._discovered_devices

    def test_add_device_duplicate(self) -> None:
        """Test adding duplicate device (should be ignored)."""
        discovery = TuyaDiscovery()
        device1 = TuyaDevice({"gwId": "dev1"}, "192.168.1.100")
        device2 = TuyaDevice({"gwId": "dev1"}, "192.168.1.100")

        discovery._add_device(device1)
        discovery._add_device(device2)

        assert len(discovery._discovered_devices) == 1


class TestTuyaDiscoveryProtocol:
    """Test TuyaDiscoveryProtocol class."""

    def test_init(self) -> None:
        """Test protocol initialization."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, "1234567890abcdef")

        assert protocol.discovery is discovery
        assert protocol.local_key == "1234567890abcdef"
        assert protocol.transport is None

    def test_connection_made(self) -> None:
        """Test connection establishment."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None)
        mock_transport = MagicMock()

        protocol.connection_made(mock_transport)

        assert protocol.transport is mock_transport

    def test_datagram_received_json(self) -> None:
        """Test receiving JSON discovery response."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None)

        response_data = {
            "gwId": "test_device",
            "productKey": "test_key",
            "version": "3.3",
        }
        data = json.dumps(response_data).encode("utf-8")

        protocol.datagram_received(data, ("192.168.1.100", 6666))

        assert len(discovery._discovered_devices) == 1
        device = list(discovery._discovered_devices.values())[0]
        assert device.device_id == "test_device"
        assert device.ip == "192.168.1.100"

    def test_datagram_received_invalid_json(self) -> None:
        """Test receiving invalid JSON (should be handled gracefully)."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None)

        data = b"not valid json"

        # Should not raise exception
        protocol.datagram_received(data, ("192.168.1.100", 6666))

        # No device should be added
        assert len(discovery._discovered_devices) == 0

    def test_datagram_received_multiple_devices(self) -> None:
        """Test receiving responses from multiple devices."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None)

        device1_data = json.dumps({"gwId": "dev1"}).encode("utf-8")
        device2_data = json.dumps({"gwId": "dev2"}).encode("utf-8")

        protocol.datagram_received(device1_data, ("192.168.1.100", 6666))
        protocol.datagram_received(device2_data, ("192.168.1.101", 6666))

        assert len(discovery._discovered_devices) == 2

    def test_error_received(self) -> None:
        """Test error handling."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None)
        error = Exception("Test error")

        # Should not raise exception
        protocol.error_received(error)


@pytest.mark.asyncio
async def test_discover_devices_convenience() -> None:
    """Test discover_devices convenience function."""
    mock_transport = MagicMock()
    mock_protocol = MagicMock()

    async def mock_create_endpoint(protocol_factory, **kwargs):
        return mock_transport, mock_protocol

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_event_loop = AsyncMock()
        mock_event_loop.create_datagram_endpoint = mock_create_endpoint
        mock_loop.return_value = mock_event_loop

        devices = await discover_devices(timeout=0.1)

        assert isinstance(devices, list)
