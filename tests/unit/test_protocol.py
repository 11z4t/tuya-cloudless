"""Unit tests for protocol module."""

from __future__ import annotations

import pytest

from lib.tuya_cloudless.protocol import TuyaLocalProtocol


@pytest.fixture
def protocol() -> TuyaLocalProtocol:
    """Create a protocol instance for testing."""
    return TuyaLocalProtocol(
        device_id="test_device_001",
        ip_address="192.168.1.100",
        local_key="test_local_key_16",
    )


def test_protocol_init(protocol: TuyaLocalProtocol) -> None:
    """Protocol should initialize with device parameters."""
    assert protocol._device_id == "test_device_001"
    assert protocol._ip_address == "192.168.1.100"


@pytest.mark.asyncio
async def test_disconnect_without_connection(protocol: TuyaLocalProtocol) -> None:
    """Disconnect should be safe without active connection."""
    await protocol.disconnect()
    assert protocol._writer is None


@pytest.mark.asyncio
async def test_get_status(protocol: TuyaLocalProtocol) -> None:
    """Get status should return dict."""
    result = await protocol.get_status()
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_set_dps(protocol: TuyaLocalProtocol) -> None:
    """Set DPS should return bool."""
    result = await protocol.set_dps({"1": True})
    assert isinstance(result, bool)
