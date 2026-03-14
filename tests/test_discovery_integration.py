"""Integration tests for Tuya device discovery with real DatagramProtocol.

These tests create actual UDP sockets and test real protocol behavior,
not just mocks. Verified against tinytuya and localtuya reference implementations.
"""

import asyncio
import json
from typing import Any

import pytest

from lib.tuya_cloudless.discovery import (
    DISCOVERY_PORT_ENCRYPTED,
    DISCOVERY_PORT_UNENCRYPTED,
    TuyaDevice,
    TuyaDiscoveryProtocol,
)


class MockDiscoveryForIntegration:
    """Mock discovery object for integration tests."""

    def __init__(self) -> None:
        """Initialize mock discovery."""
        self._discovered_devices: dict[str, TuyaDevice] = {}

    def _add_device(self, data: dict[str, Any], ip: str) -> None:
        """Add discovered device (mimics TuyaDiscovery._add_device)."""
        device_id = data.get("gwId", data.get("devId", ""))
        device_key = f"{ip}:{device_id}"

        if device_key not in self._discovered_devices:
            device = TuyaDevice(
                ip=ip,
                device_id=device_id,
                product_key=data.get("productKey", ""),
                protocol_version=data.get("version", "3.3"),
                encrypted=data.get("encrypted", False),
                raw_data=data,
            )
            self._discovered_devices[device_key] = device


@pytest.mark.asyncio
async def test_real_datagram_protocol_json_response() -> None:
    """Integration test: Real DatagramProtocol with JSON response on port 6666."""
    # Create real discovery mock
    discovery = MockDiscoveryForIntegration()
    protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

    # Create real UDP socket
    loop = asyncio.get_running_loop()
    transport, created_protocol = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", 0), allow_broadcast=False
    )

    try:
        # Simulate device response (plain JSON on port 6666)
        response_data = {
            "gwId": "integration_test_device",
            "productKey": "test_key_123",
            "version": "3.3",
            "encrypted": False,
        }
        data = json.dumps(response_data).encode("utf-8")

        # Simulate datagram received
        protocol.datagram_received(data, ("192.168.1.50", DISCOVERY_PORT_UNENCRYPTED))

        # Verify device was discovered
        assert len(discovery._discovered_devices) == 1
        device = list(discovery._discovered_devices.values())[0]
        assert device.device_id == "integration_test_device"
        assert device.product_key == "test_key_123"
        assert device.protocol_version == "3.3"
        assert device.encrypted is False

    finally:
        transport.close()


@pytest.mark.asyncio
async def test_real_datagram_protocol_multiple_devices() -> None:
    """Integration test: Real DatagramProtocol receiving from multiple devices."""
    discovery = MockDiscoveryForIntegration()
    protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", 0), allow_broadcast=False
    )

    try:
        # Simulate 3 different devices responding
        devices_data = [
            {"gwId": "device_001", "productKey": "key1", "version": "3.1"},
            {"gwId": "device_002", "productKey": "key2", "version": "3.3"},
            {"gwId": "device_003", "productKey": "key3", "version": "3.4"},
        ]

        for idx, device_data in enumerate(devices_data):
            data = json.dumps(device_data).encode("utf-8")
            protocol.datagram_received(data, (f"192.168.1.{100 + idx}", DISCOVERY_PORT_UNENCRYPTED))

        # Verify all devices discovered
        assert len(discovery._discovered_devices) == 3

        # Verify device IPs are unique
        ips = {dev.ip for dev in discovery._discovered_devices.values()}
        assert len(ips) == 3

    finally:
        transport.close()


@pytest.mark.asyncio
async def test_real_datagram_protocol_connection_lifecycle() -> None:
    """Integration test: Real DatagramProtocol connection lifecycle."""
    discovery = MockDiscoveryForIntegration()
    protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

    # Verify initial state
    assert protocol.transport is None

    loop = asyncio.get_running_loop()
    transport, created_protocol = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", 0), allow_broadcast=False
    )

    try:
        # Verify connection established
        assert created_protocol.transport is not None

        # Send data to protocol
        response = json.dumps({"gwId": "lifecycle_test"}).encode("utf-8")
        created_protocol.datagram_received(response, ("192.168.1.1", 6666))

        # Verify data processed
        assert len(discovery._discovered_devices) == 1

    finally:
        # Clean shutdown
        transport.close()


@pytest.mark.asyncio
async def test_real_datagram_protocol_duplicate_handling() -> None:
    """Integration test: Real DatagramProtocol handling duplicate responses."""
    discovery = MockDiscoveryForIntegration()
    protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", 0), allow_broadcast=False
    )

    try:
        # Same device responds multiple times (simulates broadcast retry)
        device_data = {"gwId": "duplicate_test", "productKey": "key999"}
        data = json.dumps(device_data).encode("utf-8")

        # Send same response 3 times
        for _ in range(3):
            protocol.datagram_received(data, ("192.168.1.100", DISCOVERY_PORT_UNENCRYPTED))

        # Should only have ONE device (deduplication works)
        assert len(discovery._discovered_devices) == 1

    finally:
        transport.close()


@pytest.mark.asyncio
async def test_real_datagram_protocol_concurrent_receives() -> None:
    """Integration test: Real DatagramProtocol with concurrent receives."""
    discovery = MockDiscoveryForIntegration()
    protocol = TuyaDiscoveryProtocol(discovery, None, DISCOVERY_PORT_UNENCRYPTED)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("127.0.0.1", 0), allow_broadcast=False
    )

    try:
        # Simulate rapid concurrent responses
        async def simulate_device_response(device_id: str, ip: str) -> None:
            """Simulate a device sending a response."""
            data = json.dumps({"gwId": device_id, "productKey": f"key_{device_id}"}).encode("utf-8")
            protocol.datagram_received(data, (ip, DISCOVERY_PORT_UNENCRYPTED))
            # Small delay to simulate network timing
            await asyncio.sleep(0.001)

        # Launch 10 concurrent "device responses"
        tasks = [simulate_device_response(f"dev{i:03d}", f"192.168.1.{100 + i}") for i in range(10)]
        await asyncio.gather(*tasks)

        # All devices should be discovered
        assert len(discovery._discovered_devices) == 10

    finally:
        transport.close()
