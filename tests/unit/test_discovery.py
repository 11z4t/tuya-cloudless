"""Unit tests for tuya_cloudless.discovery — DiscoveryListener, packet parsing."""

from __future__ import annotations

import asyncio
import json
import struct

import pytest
from tuya_cloudless.const import CMD_UDP, FRAME_PREFIX, FRAME_SUFFIX
from tuya_cloudless.discovery import DiscoveredDevice, DiscoveryListener

# ── DiscoveredDevice ──────────────────────────────────────────────────────────


class TestDiscoveredDevice:
    def _make_device(self) -> DiscoveredDevice:
        return DiscoveredDevice(
            gw_id="abc123",
            ip="192.168.1.10",
            version="3.3",
            product_key="testproduct",
            encrypt=True,
            active=2,
            ability=0,
        )

    def test_is_stale_fresh(self) -> None:
        device = self._make_device()
        assert not device.is_stale(max_age_seconds=300)

    def test_is_stale_old(self) -> None:
        from datetime import UTC, datetime, timedelta

        device = self._make_device()
        device.seen_at = datetime.now(UTC) - timedelta(seconds=400)
        assert device.is_stale(max_age_seconds=300)


# ── Packet parsing ────────────────────────────────────────────────────────────


def _make_discovery_packet(payload_json: dict) -> bytes:
    """Build a minimal valid Tuya discovery UDP datagram."""
    from tuya_cloudless.crypto import compute_crc32

    payload = json.dumps(payload_json).encode()
    length = len(payload) + 8
    header = struct.pack(">4sIII", FRAME_PREFIX, 1, CMD_UDP, length)
    body = header + payload
    crc = compute_crc32(body)
    return body + struct.pack(">I", crc) + FRAME_SUFFIX


class TestDiscoveryListenerParsing:
    """Test internal _parse_datagram without opening real sockets."""

    def _listener(self) -> DiscoveryListener:
        return DiscoveryListener()

    def test_parse_valid_packet(self) -> None:
        listener = self._listener()
        info = {
            "gwId": "gw_001",
            "ip": "10.0.0.1",
            "version": "3.3",
            "productKey": "pk123",
            "encrypt": True,
            "active": 2,
            "ability": 0,
        }
        raw = _make_discovery_packet(info)
        device = listener._parse_datagram(raw, "10.0.0.1")
        assert device is not None
        assert device.gw_id == "gw_001"
        assert device.ip == "10.0.0.1"
        assert device.version == "3.3"
        assert device.encrypt is True

    def test_parse_missing_gw_id_returns_none(self) -> None:
        listener = self._listener()
        raw = _make_discovery_packet({"ip": "10.0.0.2", "version": "3.1"})
        device = listener._parse_datagram(raw, "10.0.0.2")
        assert device is None

    def test_parse_too_short_returns_none(self) -> None:
        listener = self._listener()
        device = listener._parse_datagram(b"\x00" * 5, "10.0.0.3")
        assert device is None

    def test_parse_bad_prefix_returns_none(self) -> None:
        listener = self._listener()
        raw = b"\xDE\xAD\xBE\xEF" + b"\x00" * 20
        device = listener._parse_datagram(raw, "10.0.0.4")
        assert device is None

    def test_source_ip_used_as_fallback(self) -> None:
        """If packet has no 'ip' field, source IP should be used."""
        listener = self._listener()
        info = {"gwId": "gw_002", "version": "3.1"}
        raw = _make_discovery_packet(info)
        device = listener._parse_datagram(raw, "192.168.2.5")
        assert device is not None
        assert device.ip == "192.168.2.5"


# ── DiscoveryListener — get/wait API ─────────────────────────────────────────


class TestDiscoveryListenerApi:
    def test_get_unknown_returns_none(self) -> None:
        listener = DiscoveryListener()
        assert listener.get("not_there") is None

    def test_get_all_empty(self) -> None:
        listener = DiscoveryListener()
        assert listener.get_all() == []

    @pytest.mark.asyncio
    async def test_wait_for_device_timeout(self) -> None:
        listener = DiscoveryListener()
        with pytest.raises(asyncio.TimeoutError):
            await listener.wait_for_device("missing_device", timeout=0.05)
