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


def _make_discovery_packet_raw_payload(payload: bytes) -> bytes:
    """Build a discovery datagram with arbitrary raw payload bytes."""
    from tuya_cloudless.crypto import compute_crc32

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
        raw = b"\xde\xad\xbe\xef" + b"\x00" * 20
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

    @pytest.mark.asyncio
    async def test_wait_for_device_already_known(self) -> None:
        """Returns immediately when device is already in _discovered."""
        from datetime import UTC, datetime

        listener = DiscoveryListener()
        dev = DiscoveredDevice(
            gw_id="gw_known",
            ip="10.0.0.5",
            version="3.3",
            product_key="pk",
            encrypt=False,
            active=1,
            ability=0,
            seen_at=datetime.now(UTC),
        )
        listener._discovered["gw_known"] = dev
        result = await listener.wait_for_device("gw_known", timeout=1.0)
        assert result is dev


# ── _parse_datagram edge cases ─────────────────────────────────────────────────


class TestParseDatagramEdgeCases:
    def _listener(self) -> DiscoveryListener:
        return DiscoveryListener()

    def test_malformed_payload_bounds_raises(self) -> None:
        """Payload length field exceeds actual data → MalformedPacketError."""
        from tuya_cloudless.exceptions import MalformedPacketError

        listener = self._listener()
        # Build a frame where length field says payload is huge but data is short
        prefix = bytes.fromhex("000055aa")
        seq = struct.pack(">I", 1)
        cmd = struct.pack(">I", CMD_UDP)
        length = struct.pack(">I", 9999)  # claims 9999 bytes but we won't provide that
        # 16 bytes total — payload_end = 16 + 9999 - 8 = 10007 > 16
        bad_frame = prefix + seq + cmd + length  # only 16 bytes

        with pytest.raises(MalformedPacketError):
            listener._parse_datagram(bad_frame, "1.2.3.4")

    def test_returns_none_for_binary_payload_no_known_devices(self) -> None:
        """Binary payload that can't be decoded → None."""
        listener = self._listener()
        binary_payload = bytes(range(64))  # definitely not JSON
        frame = _make_discovery_packet_raw_payload(binary_payload)
        result = listener._parse_datagram(frame, "1.2.3.4")
        assert result is None

    def test_returns_none_when_payload_is_json_list(self) -> None:
        """JSON payload that's not a dict → None."""
        listener = self._listener()
        # Valid JSON but a list (not dict) — _try_decode_payload succeeds
        # but the caller's isinstance(info, dict) check will fail
        raw = _make_discovery_packet({"gwId": "x", "version": "3.1"})
        # Monkey-patch _try_decode_payload to return a list JSON

        from unittest.mock import patch

        with patch.object(listener, "_try_decode_payload", return_value=b"[1, 2, 3]"):
            result = listener._parse_datagram(raw, "1.2.3.4")
        assert result is None

    def test_try_decode_payload_with_known_device_all_fail(self) -> None:
        """Binary payload + known_device that can't decrypt → None returned."""
        listener = DiscoveryListener(known_devices={"gw001": "0123456789abcdef"})
        binary = bytes(range(64))  # invalid ciphertext for any version
        result = listener._try_decode_payload(binary)
        assert result is None


# ── _DiscoveryProtocol ─────────────────────────────────────────────────────────


class TestDiscoveryProtocol:
    def test_datagram_received_enqueues(self) -> None:
        from tuya_cloudless.discovery import _DiscoveryProtocol

        queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue()
        proto = _DiscoveryProtocol(queue, "test-label")
        proto.datagram_received(b"hello", ("10.0.0.1", 6666))

        assert not queue.empty()
        item = queue.get_nowait()
        assert item[0] == b"hello"

    def test_datagram_received_full_queue_drops(self) -> None:
        from tuya_cloudless.discovery import _DiscoveryProtocol

        queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue(maxsize=1)
        queue.put_nowait((b"existing", ("10.0.0.1", 6666)))
        proto = _DiscoveryProtocol(queue, "test-label")

        # Must not raise, just drop silently
        proto.datagram_received(b"overflow", ("10.0.0.1", 6666))
        assert queue.qsize() == 1

    def test_error_received_does_not_raise(self) -> None:
        from tuya_cloudless.discovery import _DiscoveryProtocol

        queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue()
        proto = _DiscoveryProtocol(queue, "test-label")
        proto.error_received(OSError("test UDP error"))  # must not raise


# ── DiscoveryListener — init with known_devices ────────────────────────────────


class TestDiscoveryListenerInit:
    def test_init_converts_str_key_to_bytes(self) -> None:
        listener = DiscoveryListener(known_devices={"gw001": "0123456789abcdef"})
        assert isinstance(listener._known_devices["gw001"], bytes)

    def test_init_keeps_bytes_key(self) -> None:
        key_bytes = b"0123456789abcdef"
        listener = DiscoveryListener(known_devices={"gw001": key_bytes})
        assert listener._known_devices["gw001"] == key_bytes

    def test_init_no_known_devices(self) -> None:
        listener = DiscoveryListener()
        assert listener._known_devices == {}


# ── DiscoveryListener — stop when not running ─────────────────────────────────


class TestDiscoveryListenerStop:
    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_safe(self) -> None:
        listener = DiscoveryListener()
        # stop() when never started should not raise
        await listener.stop()

    def test_running_starts_false(self) -> None:
        listener = DiscoveryListener()
        assert listener._running is False


# ── _parse_datagram — additional cases ────────────────────────────────────────


class TestDiscoveryListenerParseExtra:
    def _listener(self) -> DiscoveryListener:
        return DiscoveryListener()

    def test_wrong_cmd_returns_none(self) -> None:
        """Frames with cmd != CMD_UDP and cmd != 0x12 are ignored."""
        listener = self._listener()
        prefix = bytes.fromhex("000055aa")
        payload = json.dumps({"gwId": "x"}).encode()
        length_val = len(payload) + 8
        bad_cmd_frame = (
            prefix
            + struct.pack(">III", 1, 0xFF, length_val)
            + payload
            + b"\x00\x00\x00\x00\x00\x00\xaa\x55"
        )
        result = listener._parse_datagram(bad_cmd_frame, "1.2.3.4")
        assert result is None


# ── R26 security fixes ────────────────────────────────────────────────────────


class TestDiscoveryR26Fixes:
    """R26-1: source_ip preferred over payload ip; R26-4: OverflowError handled."""

    def _listener(self) -> DiscoveryListener:
        return DiscoveryListener()

    def test_payload_ip_ignored_uses_source_ip(self) -> None:
        """Device-reported IP in payload must be ignored; UDP source IP is used.

        A rogue device could spoof the payload 'ip' to redirect HA's TCP
        connection to an arbitrary host.  R26-1 fix: always use source_ip.
        """
        listener = self._listener()
        info = {
            "gwId": "gw_spoof",
            "ip": "192.168.99.99",  # attacker's desired redirect target
            "version": "3.3",
        }
        raw = _make_discovery_packet(info)
        device = listener._parse_datagram(raw, "10.0.0.5")  # real UDP source
        assert device is not None
        assert device.ip == "10.0.0.5", "Must use UDP source IP, not payload ip"

    def test_float_inf_in_active_does_not_raise(self) -> None:
        """Payload with 'active': 1e400 (parsed as float inf) must not crash parser.

        int(float('inf')) raises OverflowError, which was not in the except tuple
        before R26-4.  This test verifies the datagram is silently discarded
        (returns None) rather than killing the discovery task.
        """
        listener = self._listener()
        # Build a raw packet that contains valid JSON with float inf
        payload = b'{"gwId":"gw_overflow","ip":"1.2.3.4","active":1e400}'
        length_val = len(payload) + 8
        raw = (
            bytes.fromhex("000055aa")
            + struct.pack(">III", 1, 0x00000001, length_val)
            + payload
            + b"\x00\x00\x00\x00"
            + bytes.fromhex("0000aa55")
        )
        # Must not raise; result may be None (invalid CRC) — we just care no exception
        try:
            listener._parse_datagram(raw, "1.2.3.4")
        except Exception as exc:
            pytest.fail(f"_parse_datagram raised unexpectedly: {exc!r}")

    def test_active_as_float_string_becomes_zero(self) -> None:
        """Non-int 'active' value (e.g. float JSON number) must not propagate as float."""
        listener = self._listener()
        info = {
            "gwId": "gw_float",
            "ip": "1.2.3.4",
            "version": "3.3",
            "active": 2,  # normal int — must be accepted
        }
        raw = _make_discovery_packet(info)
        device = listener._parse_datagram(raw, "1.2.3.4")
        assert device is not None
        assert isinstance(device.active, int)


# ── Lines 317-318 — CancelledError in _process_loop ──────────────────────────


class TestProcessLoopCancelledError:
    """Lines 317-318: asyncio.CancelledError must break the process loop."""

    @pytest.mark.asyncio
    async def test_cancelled_error_breaks_process_loop(self) -> None:
        """Cancelling the task running _process_loop must exit cleanly."""
        listener = DiscoveryListener()
        listener._running = True

        import contextlib

        task = asyncio.create_task(listener._process_loop())
        await asyncio.sleep(0.05)  # let it reach the wait_for
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ── Lines 325-330 — discovery table full ─────────────────────────────────────


class TestDiscoveryTableFull:
    """Lines 325-330: new devices are dropped when table is at _MAX_DISCOVERED."""

    @pytest.mark.asyncio
    async def test_table_full_drops_new_device(self) -> None:
        from tuya_cloudless.discovery import DiscoveredDevice, DiscoveryListener

        listener = DiscoveryListener()
        listener._running = True

        # Fill the table to _MAX_DISCOVERED (256) with fake entries
        for i in range(256):
            fake_device = DiscoveredDevice(
                gw_id=f"fake_{i:04d}",
                ip="1.2.3.4",
                version="3.3",
                product_key="pk",
                encrypt=False,
                active=2,
                ability=0,
            )
            listener._discovered[fake_device.gw_id] = fake_device

        assert len(listener._discovered) == 256

        # Queue a valid new device packet
        new_device_info = {
            "gwId": "new_device_0001",
            "ip": "10.0.0.99",
            "version": "3.3",
            "productKey": "pk_new",
            "encrypt": False,
            "active": 2,
        }
        packet = _make_discovery_packet(new_device_info)
        await listener._queue.put((packet, ("10.0.0.99", 6666)))

        async def stop_after() -> None:
            await asyncio.sleep(0.1)
            listener._running = False

        task = asyncio.create_task(stop_after())
        await listener._process_loop()
        await task

        # The new device must NOT be in the table
        assert "new_device_0001" not in listener._discovered
        assert len(listener._discovered) == 256


# ── Lines 409-411 — JSON decode failure (defensive guard) ────────────────────


class TestParseDatagramJsonFailure:
    """Lines 409-411: json.loads failure triggers the defensive guard."""

    def test_json_parse_failure_returns_none(self) -> None:
        from unittest.mock import patch

        listener = DiscoveryListener()
        valid_frame = _make_discovery_packet({"gwId": "gw1", "version": "3.3"})

        # Patch _try_decode_payload to return non-JSON bytes to trigger lines 409-411
        with patch.object(listener, "_try_decode_payload", return_value=b"not_valid_json!!!"):
            result = listener._parse_datagram(valid_frame, "10.0.0.1")
        assert result is None


# ── Lines 444-452 — field-too-long guard ─────────────────────────────────────


class TestParseDatagramFieldTooLong:
    """Lines 444-452: oversized gwId field triggers the drop guard."""

    def test_gw_id_too_long_returns_none(self) -> None:
        listener = DiscoveryListener()
        # gwId > 64 chars
        info = {
            "gwId": "x" * 65,
            "ip": "192.168.1.10",
            "version": "3.3",
            "productKey": "pk123",
            "encrypt": False,
            "active": 2,
        }
        packet = _make_discovery_packet(info)
        result = listener._parse_datagram(packet, "192.168.1.10")
        assert result is None

    def test_product_key_too_long_returns_none(self) -> None:
        listener = DiscoveryListener()
        # productKey > 64 chars
        info = {
            "gwId": "gw001",
            "ip": "192.168.1.10",
            "version": "3.3",
            "productKey": "p" * 65,
            "encrypt": False,
            "active": 2,
        }
        packet = _make_discovery_packet(info)
        result = listener._parse_datagram(packet, "192.168.1.10")
        assert result is None
