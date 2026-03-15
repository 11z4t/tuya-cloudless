"""Extended tests for tuya_cloudless.discovery to boost coverage."""

from __future__ import annotations

import asyncio
import json
import struct
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tuya_cloudless.const import CMD_UDP, FRAME_PREFIX
from tuya_cloudless.discovery import (
    DiscoveredDevice,
    DiscoveryListener,
    _DiscoveryProtocol,
)
from tuya_cloudless.exceptions import DiscoveryError, MalformedPacketError

# ── DiscoveredDevice ────────────────────────────────────────────────────────


class TestDiscoveredDevice:
    def test_is_stale_fresh(self) -> None:
        dev = DiscoveredDevice(
            gw_id="abc",
            ip="1.2.3.4",
            version="3.3",
            product_key="pk",
            encrypt=True,
            active=2,
            ability=0,
            seen_at=datetime.now(UTC),
        )
        assert dev.is_stale() is False

    def test_is_stale_old(self) -> None:
        dev = DiscoveredDevice(
            gw_id="abc",
            ip="1.2.3.4",
            version="3.3",
            product_key="pk",
            encrypt=True,
            active=2,
            ability=0,
            seen_at=datetime.now(UTC) - timedelta(seconds=600),
        )
        assert dev.is_stale() is True

    def test_is_stale_custom_age(self) -> None:
        dev = DiscoveredDevice(
            gw_id="abc",
            ip="1.2.3.4",
            version="3.3",
            product_key="pk",
            encrypt=True,
            active=2,
            ability=0,
            seen_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        assert dev.is_stale(max_age_seconds=5) is True
        assert dev.is_stale(max_age_seconds=60) is False


# ── _DiscoveryProtocol ──────────────────────────────────────────────────────


class TestDiscoveryProtocol:
    def test_datagram_received(self) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        proto = _DiscoveryProtocol(queue, "test")
        proto.datagram_received(b"data", ("1.2.3.4", 6666))
        assert queue.qsize() == 1
        data, addr = queue.get_nowait()
        assert data == b"data"
        assert addr == ("1.2.3.4", 6666)

    def test_datagram_received_queue_full(self) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        proto = _DiscoveryProtocol(queue, "test")
        proto.datagram_received(b"first", ("1.2.3.4", 6666))
        proto.datagram_received(b"second", ("1.2.3.4", 6666))  # dropped
        assert queue.qsize() == 1

    def test_error_received(self) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        proto = _DiscoveryProtocol(queue, "test")
        proto.error_received(OSError("test error"))  # Should not raise


# ── DiscoveryListener ───────────────────────────────────────────────────────


class TestDiscoveryListener:
    def test_init_no_devices(self) -> None:
        listener = DiscoveryListener()
        assert listener.get_all() == []

    def test_init_with_known_devices_str(self) -> None:
        listener = DiscoveryListener(known_devices={"abc": "0123456789abcdef"})
        assert b"0123456789abcdef" in listener._known_devices.values()

    def test_init_with_known_devices_bytes(self) -> None:
        listener = DiscoveryListener(known_devices={"abc": b"\x00" * 16})
        assert b"\x00" * 16 in listener._known_devices.values()

    def test_get_none(self) -> None:
        listener = DiscoveryListener()
        assert listener.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        listener = DiscoveryListener()
        with patch.object(
            asyncio.get_event_loop(), "create_datagram_endpoint", new_callable=AsyncMock
        ) as mock_dgram:
            mock_transport = MagicMock()
            mock_dgram.return_value = (mock_transport, MagicMock())
            await listener.start()
            assert listener._running is True
            await listener.stop()
            assert listener._running is False

    @pytest.mark.asyncio
    async def test_start_already_running(self) -> None:
        listener = DiscoveryListener()
        listener._running = True
        await listener.start()  # Should return immediately

    @pytest.mark.asyncio
    async def test_start_socket_error(self) -> None:
        listener = DiscoveryListener()
        with (
            patch.object(
                asyncio.get_event_loop(),
                "create_datagram_endpoint",
                side_effect=OSError("bind fail"),
            ),
            pytest.raises(DiscoveryError, match="bind"),
        ):
            await listener.start()

    @pytest.mark.asyncio
    async def test_stop_no_task(self) -> None:
        listener = DiscoveryListener()
        await listener.stop()  # Should not raise


# ── _parse_datagram ─────────────────────────────────────────────────────────


def _build_discovery_packet(payload_dict: dict) -> bytes:
    """Build a fake Tuya discovery UDP packet."""
    payload = json.dumps(payload_dict).encode()
    length = len(payload) + 8  # CRC(4) + suffix(4)
    header = struct.pack(">4sIII", FRAME_PREFIX, 0, CMD_UDP, length)
    crc = struct.pack(">I", 0)
    suffix = b"\x00\x00\xaa\x55"
    return header + payload + crc + suffix


class TestParseDatagram:
    def test_too_short(self) -> None:
        listener = DiscoveryListener()
        result = listener._parse_datagram(b"\x00" * 5, "1.2.3.4")
        assert result is None

    def test_bad_prefix(self) -> None:
        listener = DiscoveryListener()
        result = listener._parse_datagram(b"\xff" * 20, "1.2.3.4")
        assert result is None

    def test_non_discovery_cmd(self) -> None:
        listener = DiscoveryListener()
        header = struct.pack(">4sIII", FRAME_PREFIX, 0, 0x99, 20)
        result = listener._parse_datagram(header + b"\x00" * 20, "1.2.3.4")
        assert result is None

    def test_valid_plaintext_packet(self) -> None:
        listener = DiscoveryListener()
        packet = _build_discovery_packet(
            {
                "gwId": "abc123",
                "ip": "192.168.1.42",
                "version": "3.3",
                "productKey": "pk123",
                "encrypt": True,
                "active": 2,
                "ability": 0,
            }
        )
        result = listener._parse_datagram(packet, "1.2.3.4")
        assert result is not None
        assert result.gw_id == "abc123"
        assert result.ip == "192.168.1.42"
        assert result.version == "3.3"
        assert result.encrypt is True

    def test_missing_gw_id(self) -> None:
        listener = DiscoveryListener()
        packet = _build_discovery_packet(
            {
                "ip": "192.168.1.42",
                "version": "3.3",
            }
        )
        result = listener._parse_datagram(packet, "1.2.3.4")
        assert result is None

    def test_uses_source_ip_fallback(self) -> None:
        listener = DiscoveryListener()
        packet = _build_discovery_packet(
            {
                "gwId": "abc123",
                "version": "3.3",
            }
        )
        result = listener._parse_datagram(packet, "10.0.0.1")
        assert result is not None
        assert result.ip == "10.0.0.1"

    def test_invalid_payload_bounds(self) -> None:
        listener = DiscoveryListener()
        # length field that exceeds data
        header = struct.pack(">4sIII", FRAME_PREFIX, 0, CMD_UDP, 0)
        with pytest.raises(MalformedPacketError):
            listener._parse_datagram(header + b"\x00" * 4, "1.2.3.4")

    def test_non_dict_json(self) -> None:
        listener = DiscoveryListener()
        payload = b"[1,2,3]"
        length = len(payload) + 8
        header = struct.pack(">4sIII", FRAME_PREFIX, 0, CMD_UDP, length)
        packet = header + payload + b"\x00" * 8
        result = listener._parse_datagram(packet, "1.2.3.4")
        assert result is None

    def test_encrypted_cmd_0x12(self) -> None:
        listener = DiscoveryListener()
        payload = json.dumps({"gwId": "enc1", "ip": "1.2.3.4", "version": "3.3"}).encode()
        length = len(payload) + 8
        header = struct.pack(">4sIII", FRAME_PREFIX, 0, 0x12, length)
        packet = header + payload + b"\x00" * 8
        result = listener._parse_datagram(packet, "1.2.3.4")
        assert result is not None
        assert result.gw_id == "enc1"


# ── _try_decode_payload ─────────────────────────────────────────────────────


class TestProcessLoop:
    @pytest.mark.asyncio
    async def test_process_loop_new_device(self) -> None:
        listener = DiscoveryListener()
        listener._running = True
        packet = _build_discovery_packet(
            {
                "gwId": "test1",
                "ip": "1.2.3.4",
                "version": "3.3",
                "productKey": "pk",
                "encrypt": True,
                "active": 2,
                "ability": 0,
            }
        )
        await listener._queue.put((packet, ("1.2.3.4", 6666)))

        async def stop_after_one() -> None:
            await asyncio.sleep(0.1)
            listener._running = False

        task = asyncio.create_task(stop_after_one())
        await listener._process_loop()
        await task
        assert "test1" in listener._discovered

    @pytest.mark.asyncio
    async def test_process_loop_update_device(self) -> None:
        listener = DiscoveryListener()
        listener._running = True
        # Pre-populate
        listener._discovered["test1"] = DiscoveredDevice(
            gw_id="test1",
            ip="old",
            version="3.3",
            product_key="pk",
            encrypt=True,
            active=2,
            ability=0,
        )
        packet = _build_discovery_packet(
            {
                "gwId": "test1",
                "ip": "1.2.3.5",
                "version": "3.3",
                "productKey": "pk",
                "encrypt": True,
                "active": 2,
                "ability": 0,
            }
        )
        await listener._queue.put((packet, ("1.2.3.5", 6666)))

        async def stop_after() -> None:
            await asyncio.sleep(0.1)
            listener._running = False

        task = asyncio.create_task(stop_after())
        await listener._process_loop()
        await task
        assert listener._discovered["test1"].ip == "1.2.3.5"

    @pytest.mark.asyncio
    async def test_process_loop_timeout(self) -> None:
        listener = DiscoveryListener()
        listener._running = True

        async def stop_after() -> None:
            await asyncio.sleep(1.5)  # Wait longer than UDP_QUEUE_TIMEOUT
            listener._running = False

        task = asyncio.create_task(stop_after())
        await listener._process_loop()
        await task

    @pytest.mark.asyncio
    async def test_process_loop_cancelled(self) -> None:
        listener = DiscoveryListener()
        listener._running = True

        async def cancel_soon() -> None:
            await asyncio.sleep(0.05)
            # Simulate cancellation by pushing a cancel into the queue processing
            listener._running = False

        task = asyncio.create_task(cancel_soon())
        await listener._process_loop()
        await task

    @pytest.mark.asyncio
    async def test_process_loop_malformed_packet(self) -> None:
        listener = DiscoveryListener()
        listener._running = True
        # Bad packet that will raise in _parse_datagram
        bad_packet = FRAME_PREFIX + struct.pack(">III", 0, CMD_UDP, 9999) + b"\x00" * 4
        await listener._queue.put((bad_packet, ("1.2.3.4", 6666)))

        async def stop_after() -> None:
            await asyncio.sleep(0.1)
            listener._running = False

        task = asyncio.create_task(stop_after())
        await listener._process_loop()
        await task
        assert len(listener._discovered) == 0


class TestWaitForDevice:
    @pytest.mark.asyncio
    async def test_wait_already_discovered(self) -> None:
        listener = DiscoveryListener()
        dev = DiscoveredDevice(
            gw_id="abc",
            ip="1.2.3.4",
            version="3.3",
            product_key="pk",
            encrypt=True,
            active=2,
            ability=0,
        )
        listener._discovered["abc"] = dev
        result = await listener.wait_for_device("abc", timeout=1.0)
        assert result.gw_id == "abc"

    @pytest.mark.asyncio
    async def test_wait_timeout(self) -> None:
        listener = DiscoveryListener()
        with pytest.raises(TimeoutError, match="not discovered"):
            await listener.wait_for_device("nonexistent", timeout=0.1)

    @pytest.mark.asyncio
    async def test_wait_discovered_during_wait(self) -> None:
        listener = DiscoveryListener()
        dev = DiscoveredDevice(
            gw_id="abc",
            ip="1.2.3.4",
            version="3.3",
            product_key="pk",
            encrypt=True,
            active=2,
            ability=0,
        )

        async def add_device_later() -> None:
            await asyncio.sleep(0.05)
            listener._discovered["abc"] = dev
            listener._device_event.set()

        task = asyncio.create_task(add_device_later())
        result = await listener.wait_for_device("abc", timeout=1.0)
        await task
        assert result.gw_id == "abc"


class TestDevicesIterator:
    @pytest.mark.asyncio
    async def test_devices_empty(self) -> None:
        listener = DiscoveryListener()
        listener._running = True

        async def stop_soon() -> None:
            await asyncio.sleep(0.2)
            listener._running = False

        task = asyncio.create_task(stop_soon())
        devices = []
        async for dev in listener.devices():
            devices.append(dev)
        await task
        assert devices == []

    @pytest.mark.asyncio
    async def test_devices_with_discovered(self) -> None:
        listener = DiscoveryListener()
        listener._running = True
        dev = DiscoveredDevice(
            gw_id="abc",
            ip="1.2.3.4",
            version="3.3",
            product_key="pk",
            encrypt=True,
            active=2,
            ability=0,
        )
        listener._discovered["abc"] = dev

        async def stop_soon() -> None:
            await asyncio.sleep(0.05)
            listener._running = False
            listener._device_event.set()  # Unblock the wait

        task = asyncio.create_task(stop_soon())
        devices = []
        async for d in listener.devices():
            devices.append(d)
            break  # Just get one
        await task
        assert len(devices) == 1
        assert devices[0].gw_id == "abc"


class TestTryDecodePayload:
    def test_plain_json(self) -> None:
        listener = DiscoveryListener()
        payload = b'{"gwId": "abc"}'
        result = listener._try_decode_payload(payload)
        assert result == payload

    def test_not_json_no_keys(self) -> None:
        listener = DiscoveryListener()
        result = listener._try_decode_payload(b"\x00\xff\xfe")
        assert result is None

    def test_decrypt_with_known_key(self) -> None:
        from tuya_cloudless.crypto import encrypt_payload

        local_key = b"0123456789abcdef"
        plaintext = b'{"gwId": "abc"}'
        ciphertext = encrypt_payload("3.3", local_key, plaintext)
        listener = DiscoveryListener(known_devices={"abc": local_key})
        result = listener._try_decode_payload(ciphertext)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["gwId"] == "abc"
