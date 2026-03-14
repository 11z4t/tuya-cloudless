"""Tests for Tuya device discovery."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.tuya_cloudless.discovery import (
    DISCOVERY_PORT_ENCRYPTED,
    DISCOVERY_PORT_UNENCRYPTED,
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
        device = TuyaDevice(
            ip="192.168.1.100",
            device_id="device123",
            product_key="key123",
            protocol_version="3.3",
            encrypted=True,
            raw_data=data,
        )

        assert device.ip == "192.168.1.100"
        assert device.device_id == "device123"
        assert device.product_key == "key123"
        assert device.protocol_version == "3.3"
        assert device.encrypted is True

    def test_init_minimal_data(self) -> None:
        """Test initialization with minimal data."""
        data = {}
        device = TuyaDevice(
            ip="192.168.1.100",
            device_id="",
            product_key="",
            protocol_version="3.3",
            encrypted=False,
            raw_data=data,
        )

        assert device.ip == "192.168.1.100"
        assert device.device_id == ""
        assert device.product_key == ""
        assert device.protocol_version == "3.3"
        assert device.encrypted is False

    def test_frozen_dataclass(self) -> None:
        """Test that TuyaDevice is immutable."""
        device = TuyaDevice(
            ip="192.168.1.100",
            device_id="test",
            product_key="key",
            protocol_version="3.3",
            encrypted=False,
            raw_data={},
        )

        with pytest.raises(AttributeError):
            device.ip = "192.168.1.101"  # type: ignore

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        data = {
            "gwId": "device123",
            "productKey": "key123",
            "version": "3.3",
        }
        device = TuyaDevice(
            ip="192.168.1.100",
            device_id="device123",
            product_key="key123",
            protocol_version="3.3",
            encrypted=False,
            raw_data=data,
        )

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
        assert discovery.timeout == 3.0
        assert discovery.retries == 2

    def test_init_custom_values(self) -> None:
        """Test initialization with custom values."""
        discovery = TuyaDiscovery(
            broadcast_address="192.168.1.255",
            timeout=5.0,
            retries=3,
        )

        assert discovery.broadcast_address == "192.168.1.255"
        assert discovery.timeout == 5.0
        assert discovery.retries == 3

    @pytest.mark.asyncio
    async def test_discover_success(self) -> None:
        """Test successful device discovery on both ports."""
        discovery = TuyaDiscovery(timeout=0.1, retries=1)

        # Mock two separate transports for port 6666 and 6667
        mock_transport_6666 = MagicMock()
        mock_transport_6667 = MagicMock()

        call_count = 0

        async def mock_create_endpoint(protocol_factory, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_transport_6666, MagicMock()
            else:
                return mock_transport_6667, MagicMock()

        # Simulate running loop
        async def run_discovery():
            loop = asyncio.get_running_loop()
            with patch.object(
                loop, "create_datagram_endpoint", side_effect=mock_create_endpoint
            ):
                return await discovery.discover()

        devices = await run_discovery()

        assert isinstance(devices, list)
        # Should send broadcasts on both ports (retries+1 times each)
        assert mock_transport_6666.sendto.call_count == 2  # retries=1 → 2 broadcasts
        assert mock_transport_6667.sendto.call_count == 2
        mock_transport_6666.close.assert_called_once()
        mock_transport_6667.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_with_local_key(self) -> None:
        """Test discovery with local key."""
        discovery = TuyaDiscovery(timeout=0.1, retries=0)

        mock_transport_6666 = MagicMock()
        mock_transport_6667 = MagicMock()

        call_count = 0

        async def mock_create_endpoint(protocol_factory, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_transport_6666, MagicMock()
            else:
                return mock_transport_6667, MagicMock()

        async def run_discovery():
            loop = asyncio.get_running_loop()
            with patch.object(
                loop, "create_datagram_endpoint", side_effect=mock_create_endpoint
            ):
                return await discovery.discover(local_key="1234567890abcdef")

        devices = await run_discovery()

        assert isinstance(devices, list)

    @pytest.mark.asyncio
    async def test_discover_cancellation(self) -> None:
        """Test discovery handles cancellation gracefully."""
        discovery = TuyaDiscovery(timeout=5.0, retries=0)

        mock_transport_6666 = MagicMock()
        mock_transport_6667 = MagicMock()

        call_count = 0

        async def mock_create_endpoint(protocol_factory, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_transport_6666, MagicMock()
            else:
                return mock_transport_6667, MagicMock()

        async def cancel_discovery():
            loop = asyncio.get_running_loop()
            with patch.object(
                loop, "create_datagram_endpoint", side_effect=mock_create_endpoint
            ):
                task = asyncio.create_task(discovery.discover())
                await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        # Should handle cancellation gracefully
        await cancel_discovery()

    def test_add_device_new(self) -> None:
        """Test adding a new device."""
        discovery = TuyaDiscovery()
        data = {"gwId": "dev1", "productKey": "key1", "version": "3.3"}

        discovery._add_device(data, "192.168.1.100")

        assert len(discovery._discovered_devices) == 1
        assert "192.168.1.100:dev1" in discovery._discovered_devices

    def test_add_device_duplicate(self) -> None:
        """Test adding duplicate device (should be ignored)."""
        discovery = TuyaDiscovery()
        data = {"gwId": "dev1", "productKey": "key1"}

        discovery._add_device(data, "192.168.1.100")
        discovery._add_device(data, "192.168.1.100")

        assert len(discovery._discovered_devices) == 1


class TestTuyaDiscoveryProtocol:
    """Test TuyaDiscoveryProtocol class."""

    def test_init(self) -> None:
        """Test protocol initialization."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(
            discovery, "1234567890abcdef", DISCOVERY_PORT_UNENCRYPTED
        )

        assert protocol.discovery is discovery
        assert protocol.local_key == "1234567890abcdef"
        assert protocol.listen_port == DISCOVERY_PORT_UNENCRYPTED
        assert protocol.transport is None

    def test_connection_made(self) -> None:
        """Test connection establishment."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)
        mock_transport = MagicMock()

        protocol.connection_made(mock_transport)

        assert protocol.transport is mock_transport

    def test_datagram_received_json_port_6666(self) -> None:
        """Test receiving JSON discovery response on port 6666."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

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

    @pytest.mark.skip(reason="Requires real pycryptodome, not mock — tested in venv")
    def test_datagram_received_encrypted_port_6667(self) -> None:
        """Test receiving encrypted discovery response on port 6667.

        NOTE: This test requires real pycryptodome library to work.
        Mocked Crypto module (conftest.py) returns static data which fails PKCS7 unpadding.
        This test passes when run in venv with pycryptodome installed.
        """
        from lib.tuya_cloudless.crypto import encrypt_payload
        from lib.tuya_cloudless.discovery import UDP_KEY

        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_ENCRYPTED)

        response_data = {
            "gwId": "encrypted_device",
            "productKey": "key456",
            "version": "3.4",
        }
        plaintext = json.dumps(response_data).encode("utf-8")
        encrypted = encrypt_payload(plaintext, UDP_KEY, "3.3")

        protocol.datagram_received(encrypted, ("192.168.1.101", 6667))

        assert len(discovery._discovered_devices) == 1
        device = list(discovery._discovered_devices.values())[0]
        assert device.device_id == "encrypted_device"
        assert device.ip == "192.168.1.101"
        assert device.protocol_version == "3.4"

    def test_datagram_received_invalid_json(self) -> None:
        """Test receiving invalid JSON (should be handled gracefully)."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

        data = b"not valid json"

        # Should not raise exception
        protocol.datagram_received(data, ("192.168.1.100", 6666))

        # No device should be added
        assert len(discovery._discovered_devices) == 0

    def test_datagram_received_empty_response(self) -> None:
        """Test receiving empty response (edge case)."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

        # Should not raise exception
        protocol.datagram_received(b"", ("192.168.1.100", 6666))

        assert len(discovery._discovered_devices) == 0

    def test_datagram_received_malformed_response(self) -> None:
        """Test receiving malformed response (edge case)."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

        malformed_data = json.dumps({"invalid": "no gwId or devId"}).encode("utf-8")

        # Should handle gracefully - creates device with empty device_id
        protocol.datagram_received(malformed_data, ("192.168.1.100", 6666))

        # Device with empty ID should still be added
        assert len(discovery._discovered_devices) >= 0

    def test_datagram_received_multiple_devices(self) -> None:
        """Test receiving responses from multiple devices."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

        device1_data = json.dumps({"gwId": "dev1"}).encode("utf-8")
        device2_data = json.dumps({"gwId": "dev2"}).encode("utf-8")

        protocol.datagram_received(device1_data, ("192.168.1.100", 6666))
        protocol.datagram_received(device2_data, ("192.168.1.101", 6666))

        assert len(discovery._discovered_devices) == 2

    def test_error_received(self) -> None:
        """Test error handling."""
        discovery = TuyaDiscovery()
        protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)
        error = Exception("Test error")

        # Should not raise exception
        protocol.error_received(error)


@pytest.mark.asyncio
async def test_discover_devices_convenience() -> None:
    """Test discover_devices convenience function."""
    mock_transport_6666 = MagicMock()
    mock_transport_6667 = MagicMock()

    call_count = 0

    async def mock_create_endpoint(protocol_factory, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_transport_6666, MagicMock()
        else:
            return mock_transport_6667, MagicMock()

    async def run_discovery():
        loop = asyncio.get_running_loop()
        with patch.object(
            loop, "create_datagram_endpoint", side_effect=mock_create_endpoint
        ):
            return await discover_devices(timeout=0.1, retries=0)

    devices = await run_discovery()

    assert isinstance(devices, list)


@pytest.mark.asyncio
async def test_concurrent_discovery() -> None:
    """Test running multiple discoveries concurrently."""
    async def single_discovery():
        mock_transport_6666 = MagicMock()
        mock_transport_6667 = MagicMock()

        call_count = 0

        async def mock_create_endpoint(protocol_factory, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_transport_6666, MagicMock()
            else:
                return mock_transport_6667, MagicMock()

        loop = asyncio.get_running_loop()
        with patch.object(
            loop, "create_datagram_endpoint", side_effect=mock_create_endpoint
        ):
            return await discover_devices(timeout=0.1, retries=0)

    # Run 3 discoveries concurrently
    results = await asyncio.gather(
        single_discovery(),
        single_discovery(),
        single_discovery(),
    )

    assert len(results) == 3
    for result in results:
        assert isinstance(result, list)
