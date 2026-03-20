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
    "gwId":  "abc123def456",  # pragma: allowlist secret
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
import contextlib
import json
import logging
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tuya_cloudless.const import (
    CMD_UDP,
    DISCOVERY_DEVICE_STALE_AGE,
    DISCOVERY_POLLING_INTERVAL,
    DISCOVERY_WAIT_TIMEOUT,
    FRAME_HEADER_SIZE,
    FRAME_PREFIX,
    PROTOCOL_31,
    UDP_ENC_PORT,
    UDP_PORT,
    UDP_QUEUE_TIMEOUT,
)
from tuya_cloudless.crypto import decrypt_payload
from tuya_cloudless.exceptions import CryptoError, DiscoveryError, MalformedPacketError

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "DiscoveredDevice",
    "DiscoveryListener",
]

# Protect against UDP amplification / memory exhaustion from spoofed packets
_MAX_DISCOVERED = 256
# R43-F5: Cap decryption trials per datagram to bound CPU cost of forged UDP
# packets on the LAN.  Each trial attempts 3 AES ops (v3.3/v3.4/v3.5).
# 16 devices x 3 versions = 48 AES calls worst-case per packet; acceptable.
# Installations with >16 devices still work; old devices broadcast plain-JSON.
_MAX_DECRYPT_ATTEMPTS = 16
_MAX_GW_ID_LEN = 64
_MAX_VERSION_LEN = 16
_MAX_PRODUCT_KEY_LEN = 64

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
    raw_data: dict[str, object] = field(default_factory=dict, repr=False)

    def is_stale(self, max_age_seconds: float = DISCOVERY_DEVICE_STALE_AGE) -> bool:
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
        """Enqueue a received UDP datagram for processing by DiscoveryListener.

        Args:
            data: Raw UDP payload bytes.
            addr: (host, port) tuple of the sender.
        """
        _LOGGER.debug("[%s] UDP datagram from %s:%d (%d bytes)", self._label, *addr, len(data))
        try:
            self._queue.put_nowait((data, addr))
        except asyncio.QueueFull:
            _LOGGER.warning("[%s] Discovery queue full — dropping datagram", self._label)

    def error_received(self, exc: Exception) -> None:
        """Log a non-fatal UDP transport error.

        Args:
            exc: The exception reported by the asyncio transport.
        """
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
                self._known_devices[gw_id] = key.encode() if isinstance(key, str) else key

        self._interface = interface
        self._queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue(
            maxsize=queue_size
        )
        self._discovered: dict[str, DiscoveredDevice] = {}
        self._device_event: asyncio.Event = asyncio.Event()
        self._transports: list[asyncio.BaseTransport] = []
        self._running = False
        self._task: asyncio.Task[None] | None = None

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
                bind_addr = self._interface or "0.0.0.0"  # nosec B104 — UDP discovery must bind on all interfaces when no specific interface is set
                transport, _ = await loop.create_datagram_endpoint(
                    lambda p=port: _DiscoveryProtocol(self._queue, f"udp:{p}"),  # type: ignore[misc]
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
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
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
        timeout: float = DISCOVERY_WAIT_TIMEOUT,
    ) -> DiscoveredDevice:
        """Wait until a specific device is discovered.

        Args:
            gw_id: Device gateway ID to wait for.
            timeout: Maximum seconds to wait.

        Returns:
            :class:`DiscoveredDevice` once discovered.

        Raises:
            TimeoutError: If device not found within ``timeout``.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            device = self._discovered.get(gw_id)
            if device is not None:
                return device
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"Device {gw_id!r} not discovered within {timeout}s")
            self._device_event.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._device_event.wait(), timeout=remaining)

    async def devices(self) -> AsyncIterator[DiscoveredDevice]:
        """Async iterator that yields devices as they are discovered or updated.

        Each device is yielded once on initial discovery and again only when
        it is updated (e.g. IP change). Devices already yielded are not
        re-emitted on unrelated discovery events.

        Yields:
            :class:`DiscoveredDevice` for each new or updated device.
        """
        # Track the seen_at timestamp when each device was last yielded
        last_yielded: dict[str, object] = {}

        while self._running:
            # Yield any device not yet seen or whose seen_at has advanced
            for dev in list(self._discovered.values()):
                if last_yielded.get(dev.gw_id) is not dev.seen_at:
                    last_yielded[dev.gw_id] = dev.seen_at
                    yield dev

            # Wait for the next discovery event (with timeout to check _running)
            self._device_event.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._device_event.wait(),
                    timeout=DISCOVERY_POLLING_INTERVAL * 10,
                )

    # ── Internal processing ───────────────────────────────────────────────────

    async def _process_loop(self) -> None:
        """Background task: drain queue and parse discovery datagrams."""
        while self._running:
            try:
                data, addr = await asyncio.wait_for(self._queue.get(), timeout=UDP_QUEUE_TIMEOUT)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                device = self._parse_datagram(data, addr[0])
                if device is not None:
                    is_new = device.gw_id not in self._discovered
                    if is_new and len(self._discovered) >= _MAX_DISCOVERED:
                        _LOGGER.warning(
                            "Discovery table full (%d entries) — ignoring new device %s",
                            _MAX_DISCOVERED,
                            device.gw_id,
                        )
                        continue
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
            except (
                MalformedPacketError,
                struct.error,
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
                TypeError,
                KeyError,
                OverflowError,  # int(float("inf")) from malformed JSON numeric fields
            ):
                # R51-F6: Do NOT pass exc_info=True — the formatted traceback can include
                # local_key bytes present in crypto call-frames (e.g. derive_ecb_key),
                # leaking key material to log collectors / HA diagnostic dumps.
                _LOGGER.debug("Failed to parse discovery datagram from %s", addr[0])

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

        _, _, cmd, length = struct.unpack_from(">4sIII", data, 0)

        if cmd not in (CMD_UDP, 0x12):  # 0x12 = encrypted discovery
            _LOGGER.debug("Discovery datagram cmd=0x%02x is not a discovery command", cmd)
            return None

        # R42-F1: Try both checksum overheads to support v3.4/v3.5 encrypted discovery.
        # v3.1/v3.3 use CRC32 (4 bytes) + suffix (4 bytes) = 8 bytes overhead.
        # v3.4/v3.5 use HMAC-SHA256 (32 bytes) + suffix (4 bytes) = 36 bytes overhead.
        # The `length` field encodes (payload + checksum + suffix), so we subtract the
        # appropriate overhead to isolate the payload bytes.
        json_bytes: bytes | None = None
        for overhead in (8, 36):
            candidate_end = FRAME_HEADER_SIZE + length - overhead
            if FRAME_HEADER_SIZE < candidate_end <= len(data):
                decoded = self._try_decode_payload(data[FRAME_HEADER_SIZE:candidate_end])
                if decoded is not None:
                    json_bytes = decoded
                    break
        if json_bytes is None:
            # Raise for structurally invalid frames: length too small to hold
            # even the minimal 8-byte overhead, or length too large for packet.
            min_end = FRAME_HEADER_SIZE + length - 8
            if min_end <= FRAME_HEADER_SIZE or min_end > len(data):
                raise MalformedPacketError(f"Discovery payload bounds invalid (length={length})")
            return None

        try:
            info = json.loads(json_bytes.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _LOGGER.debug("Discovery payload JSON parse failed: %s", exc)
            return None

        if not isinstance(info, dict):
            return None

        gw_id: str = info.get("gwId", "")
        # Always use the actual UDP sender address as the device IP (R26-1).
        # The payload "ip" field is the device's self-reported address, which can be
        # forged by any LAN host that knows (or guesses) a target gwId.  Using the
        # real sender IP prevents an attacker from redirecting HA's TCP connection
        # to an arbitrary host via a crafted broadcast.
        ip: str = source_ip
        version: str = str(info.get("version", PROTOCOL_31))
        product_key: str = info.get("productKey", "")
        encrypt: bool = bool(info.get("encrypt", False))
        # Guard against OverflowError: int(float("inf")) raises OverflowError (not
        # ValueError) when a rogue packet sends JSON floats like 1e400.  Coerce to
        # int only when the value is a plain int; treat floats/strings as zero.
        _raw_active = info.get("active", 0)
        _raw_ability = info.get("ability", 0)
        active: int = int(_raw_active) if isinstance(_raw_active, int) else 0
        ability: int = int(_raw_ability) if isinstance(_raw_ability, int) else 0

        if not gw_id:
            _LOGGER.debug("Discovery packet missing gwId")
            return None

        if (
            len(gw_id) > _MAX_GW_ID_LEN
            or len(version) > _MAX_VERSION_LEN
            or len(product_key) > _MAX_PRODUCT_KEY_LEN
            or len(ip) > 45  # max length for IPv6 address
        ):
            _LOGGER.debug(
                "Discovery packet field too long "
                "(gwId=%d, ip=%d, version=%d, productKey=%d) — discarded",
                len(gw_id),
                len(ip),
                len(version),
                len(product_key),
            )
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

        # Try decrypting with known device keys (capped to _MAX_DECRYPT_ATTEMPTS
        # to prevent LAN DoS via crafted UDP packets on busy installations).
        for _gw_id, local_key in list(self._known_devices.items())[:_MAX_DECRYPT_ATTEMPTS]:
            for version in ("3.3", "3.4", "3.5"):
                try:
                    decrypted = decrypt_payload(version, local_key, raw)
                    # Validate it's JSON
                    json.loads(decrypted.decode("utf-8", errors="strict"))
                    # R31-3: Don't log gwId — it creates a key↔device association
                    # in log files that is sensitive diagnostic data.
                    _LOGGER.debug("Discovery payload decrypted with a known device key")
                    return decrypted
                except (CryptoError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    continue

        _LOGGER.debug("Could not decode discovery payload (%d bytes)", len(raw))
        return None
