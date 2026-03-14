"""UDP discovery listener for Tuya LAN protocol.

Tuya devices broadcast discovery packets on UDP when they come online or
on a configurable heartbeat interval.

Port mapping:
  6666 — unencrypted discovery (v3.1/3.2)
  6667 — encrypted discovery (v3.3+)

Discovery packet wire format (same as TCP frame):
  prefix(4) + seq(4) + cmd(4=0x00) + length(4) + payload + crc(4) + suffix(4)

The payload is a JSON object with at minimum:
  {
    "ip":    "192.168.1.42",
    "gwId":  "abc123def456",
    "active": 2,
    "ability": 0,
    "mode": 0,
    "encrypt": true,
    "productKey": "xxxxxxxx",
    "version": "3.3"
  }

Usage::

    listener = DiscoveryListener(known_devices={"gwId": "localkey", ...})
    await listener.start()
    async for device in listener.devices():
        print(device)
    await listener.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import AsyncIterator

from tuya_cloudless.const import (
    CMD_UDP,
    FRAME_PREFIX,
    FRAME_HEADER_SIZE,
    PROTOCOL_31,
    UDP_ENC_PORT,
    UDP_PORT,
    VERSIONS_GCM,
)
from tuya_cloudless.crypto import decrypt_payload
from tuya_cloudless.exceptions import CryptoError, DiscoveryError, MalformedPacketError

_LOGGER = logging.getLogger(__name__)

# ── Device info dataclass ─────────────────────────────────────────────────────


@dataclass
class DiscoveredDevice:
    """Information received from a Tuya UDP discovery broadcast.

    Attributes:
        gw_id:        Device gateway ID (unique device identifier).
        ip:           IPv4 address of the device.
        version:      Tuya protocol version string (e.g. "3.3").
        product_key:  Tuya product key (model identifier).
        encrypt:      True if the device uses encrypted LAN protocol.
        active:       Active status integer from broadcast.
        ability:      Capability bitmask from broadcast.
        seen_at:      UTC timestamp of the most recent discovery packet.
        raw_data:     Full parsed JSON from the discovery payload.
    """

    gw_id: str
    ip: str
    version: str
    product_key: str
    encrypt: bool
    active: int
    ability: int
    seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_data: dict = field(default_factory=dict, repr=False)

    def is_stale(self, max_age_seconds: float = 300.0) -> bool:
        """Return True if this device has not been seen within ``max_age_seconds``."""
        age = (datetime.now(UTC) - self.seen_at).total_seconds()
        return age > max_age_seconds


# ── UDP protocol implementation ───────────────────────────────────────────────


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """asyncio UDP protocol handler — receives Tuya discovery datagrams."""

    def __init__(
        self,
        queue: asyncio.Queue[tuple[bytes, tuple[str, int]]],
        label: str,
    ) -> None:
        self._queue = queue
        self._label = label

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        _LOGGER.debug("[%s] UDP datagram from %s:%d (%d bytes)", self._label, *addr, len(data))
        try:
            self._queue.put_nowait((data, addr))
        except asyncio.QueueFull:
            _LOGGER.warning("[%s] Discovery queue full — dropping datagram", self._label)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning("[%s] UDP error: %s", self._label, exc)


# ── DiscoveryListener ─────────────────────────────────────────────────────────


class DiscoveryListener:
    """Listens on UDP 6666 and 6667 for Tuya device discovery broadcasts.

    Thread-safety: All public methods are coroutines and must be called
    from the same asyncio event loop.

    Args:
        known_devices:  Mapping of gwId → local_key (bytes or hex string).
                        Required only for decrypting v3.3+ encrypted broadcasts.
        interface:      Network interface IP to bind to (empty = all interfaces).
        queue_size:     Maximum pending discovery events before dropping.
    """

    def __init__(
        self,
        known_devices: dict[str, bytes | str] | None = None,
        interface: str = "",
        queue_size: int = 256,
    ) -> None:
        self._known_devices: dict[str, bytes] = {}
        if known_devices:
            for gw_id, key in known_devices.items():
                self._known_devices[gw_id] = (
                    key.encode() if isinstance(key, str) else key
                )

        self._interface = interface
        self._queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue(
            maxsize=queue_size
        )
        self._discovered: dict[str, DiscoveredDevice] = {}
        self._device_event: asyncio.Event = asyncio.Event()
        self._transports: list[asyncio.BaseTransport] = []
        self._running = False
        self._task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start listening on UDP 6666 and 6667.

        Raises:
            DiscoveryError: If socket creation fails.
        """
        if self._running:
            return
        loop = asyncio.get_running_loop()

        for port in (UDP_PORT, UDP_ENC_PORT):
            try:
                bind_addr = self._interface or "0.0.0.0"  # noqa: S104 — intentional UDP listen
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _DiscoveryProtocol(self._queue, f"udp:{port}"),
                    local_addr=(bind_addr, port),
                    allow_broadcast=True,
                    reuse_port=True,
                )
                self._transports.append(transport)
                _LOGGER.info("Tuya discovery: listening on UDP %s:%d", bind_addr, port)
            except OSError as exc:
                raise DiscoveryError(
                    f"Failed to bind UDP discovery socket on port {port}: {exc}"
                ) from exc

        self._running = True
        self._task = asyncio.create_task(self._process_loop(), name="tuya-discovery")

    async def stop(self) -> None:
        """Stop the discovery listener and close sockets."""
        self._running = False
        for transport in self._transports:
            transport.close()
        self._transports.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _LOGGER.info("Tuya discovery: stopped")

    # ── Device enumeration ────────────────────────────────────────────────────

    def get_all(self) -> list[DiscoveredDevice]:
        """Return a snapshot of all currently known devices.

        Returns:
            List of :class:`DiscoveredDevice` objects.
        """
        return list(self._discovered.values())

    def get(self, gw_id: str) -> DiscoveredDevice | None:
        """Return a specific device by gateway ID, or None.

        Args:
            gw_id: Device gateway ID.

        Returns:
            :class:`DiscoveredDevice` or ``None`` if not yet discovered.
        """
        return self._discovered.get(gw_id)

    async def wait_for_device(
        self,
        gw_id: str,
        timeout: float = 30.0,
    ) -> DiscoveredDevice:
        """Wait until a specific device is discovered.

        Args:
            gw_id: Device gateway ID to wait for.
            timeout: Maximum seconds to wait.

        Returns:
            :class:`DiscoveredDevice` once discovered.

        Raises:
            asyncio.TimeoutError: If device not found within ``timeout``.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            device = self._discovered.get(gw_id)
            if device is not None:
                return device
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"Device {gw_id!r} not discovered within {timeout}s"
                )
            self._device_event.clear()
            try:
                await asyncio.wait_for(self._device_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass

    async def devices(self) -> AsyncIterator[DiscoveredDevice]:
        """Async iterator that yields devices as they are discovered or updated.

        Yields new or updated :class:`DiscoveredDevice` objects indefinitely
        until the listener is stopped.

        Yields:
            :class:`DiscoveredDevice` for each discovery event.
        """
        while self._running:
            self._device_event.clear()
            yield_batch = list(self._discovered.values())
            for dev in yield_batch:
                yield dev
            if not yield_batch:
                await asyncio.sleep(0.1)
                continue
            await self._device_event.wait()

    # ── Internal processing ───────────────────────────────────────────────────

    async def _process_loop(self) -> None:
        """Background task: drain queue and parse discovery datagrams."""
        while self._running:
            try:
                data, addr = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                device = self._parse_datagram(data, addr[0])
                if device is not None:
                    is_new = device.gw_id not in self._discovered
                    self._discovered[device.gw_id] = device
                    self._device_event.set()
                    if is_new:
                        _LOGGER.info(
                            "Discovered Tuya device: gwId=%s ip=%s version=%s",
                            device.gw_id,
                            device.ip,
                            device.version,
                        )
                    else:
                        _LOGGER.debug(
                            "Updated Tuya device: gwId=%s ip=%s",
                            device.gw_id,
                            device.ip,
                        )
            except (MalformedPacketError, struct.error, json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, KeyError):
                _LOGGER.debug("Failed to parse discovery datagram from %s", addr[0], exc_info=True)

    def _parse_datagram(self, data: bytes, source_ip: str) -> DiscoveredDevice | None:
        """Parse a raw UDP datagram into a :class:`DiscoveredDevice`.

        Args:
            data: Raw UDP payload bytes.
            source_ip: Source IP address (fallback if payload lacks 'ip').

        Returns:
            :class:`DiscoveredDevice` or None if parsing fails.
        """
        if len(data) < FRAME_HEADER_SIZE:
            _LOGGER.debug("Discovery datagram too short: %d bytes", len(data))
            return None

        # Validate prefix
        if data[:4] != FRAME_PREFIX:
            _LOGGER.debug("Discovery datagram has bad prefix: %s", data[:4].hex())
            return None

        import struct
        _, _, cmd, length = struct.unpack_from(">4sIII", data, 0)

        if cmd not in (CMD_UDP, 0x12):  # 0x12 = encrypted discovery
            _LOGGER.debug("Discovery datagram cmd=0x%02x is not a discovery command", cmd)
            return None

        payload_end = FRAME_HEADER_SIZE + length - 8  # exclude CRC(4) + suffix(4)
        if payload_end > len(data) or payload_end <= FRAME_HEADER_SIZE:
            raise MalformedPacketError(f"Discovery payload bounds invalid (length={length})")

        raw_payload = data[FRAME_HEADER_SIZE:payload_end]

        # Attempt to decode: try plain JSON first, then decrypt
        json_bytes = self._try_decode_payload(raw_payload)
        if json_bytes is None:
            return None

        try:
            info = json.loads(json_bytes.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _LOGGER.debug("Discovery payload JSON parse failed: %s", exc)
            return None

        if not isinstance(info, dict):
            return None

        gw_id: str = info.get("gwId", "")
        ip: str = info.get("ip", source_ip)
        version: str = str(info.get("version", PROTOCOL_31))
        product_key: str = info.get("productKey", "")
        encrypt: bool = bool(info.get("encrypt", False))
        active: int = int(info.get("active", 0))
        ability: int = int(info.get("ability", 0))

        if not gw_id:
            _LOGGER.debug("Discovery packet missing gwId")
            return None

        return DiscoveredDevice(
            gw_id=gw_id,
            ip=ip,
            version=version,
            product_key=product_key,
            encrypt=encrypt,
            active=active,
            ability=ability,
            seen_at=datetime.now(UTC),
            raw_data=info,
        )

    def _try_decode_payload(self, raw: bytes) -> bytes | None:
        """Try to decode a discovery payload.

        Attempts in order:
        1. Plain UTF-8 JSON (v3.1/3.2 unencrypted)
        2. Decrypt with each known device key (v3.3+ encrypted)

        Args:
            raw: Raw payload bytes from discovery datagram.

        Returns:
            JSON bytes if successfully decoded, None otherwise.
        """
        # Try plain JSON first
        try:
            json.loads(raw.decode("utf-8", errors="strict"))
            return raw
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Try decrypting with known device keys
        for gw_id, local_key in self._known_devices.items():
            for version in ("3.3", "3.4", "3.5"):
                try:
                    decrypted = decrypt_payload(version, local_key, raw)
                    # Validate it's JSON
                    json.loads(decrypted.decode("utf-8", errors="strict"))
                    _LOGGER.debug("Discovery payload decrypted with key for gwId=%s", gw_id)
                    return decrypted
                except (CryptoError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    continue

        _LOGGER.debug("Could not decode discovery payload (%d bytes)", len(raw))
        return None
