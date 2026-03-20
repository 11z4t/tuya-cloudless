"""Tuya BLE WiFi provisioning library.

Implements the Tuya BLE GATT protocol for initial device provisioning:

  1. BLE GATT connection + characteristic discovery
  2. Handshake — exchange random nonces, derive session key (AES-128-ECB)
  3. WiFi credential + activation token delivery (encrypted with session key)
  4. Pairing result confirmation — device ACKs, then connects to WiFi
  5. Device calls ``activator_url`` → local_key is issued by fake-cloud server

This module is BLE-hardware-agnostic for frame encoding/decoding.
The :class:`BleProvisioner` class requires *bleak*
(``pip install bleak``) for actual BLE communication.

BLE transport limits frames to 20 bytes (default ATT MTU).  Larger
messages are chunked and reassembled transparently.

References:
  - Tuya BLE provisioning protocol (developer.tuya.com)
  - tinytuya BLE: https://github.com/jasonacox/tinytuya
  - TuyaOS open-source SDK: https://github.com/tuya/tuyaos-development-board-t2
"""

from __future__ import annotations

import json
import os
import secrets
import struct
from dataclasses import dataclass, field
from typing import Final

from .crypto import encrypt_ble_payload
from .exceptions import PairingError

__all__ = [
    "BLE_MAX_FRAME_SIZE",
    "BLE_NOTIFY_CHAR_UUID",
    "BLE_SERVICE_UUID",
    "BLE_WRITE_CHAR_UUID",
    "CMD_HANDSHAKE",
    "CMD_HANDSHAKE_RESP",
    "CMD_PAIR_FAIL",
    "CMD_PAIR_SUCCESS",
    "CMD_WIFI_CONFIG",
    "CMD_WIFI_CONFIG_RESP",
    "BleFrame",
    "BleProvisioner",
    "ProvisionPayload",
    "build_provision_frames",
    "crc16_modbus",
    "parse_ble_response",
]

# ── BLE GATT identifiers ────────────────────────────────────────────────────

#: Primary service for Tuya BLE provisioning
BLE_SERVICE_UUID: Final[str] = "0000fd50-0000-1000-8000-00805f9b34fb"

#: Write-without-response characteristic (controller → device)
BLE_WRITE_CHAR_UUID: Final[str] = "00000001-0000-1001-8001-00805f9b07d0"

#: Notify characteristic (device → controller)
BLE_NOTIFY_CHAR_UUID: Final[str] = "00000002-0000-1001-8001-00805f9b07d0"

# ── Frame / transport constants ─────────────────────────────────────────────

#: Default ATT MTU — BLE frames MUST NOT exceed this (20 bytes of payload per chunk)
BLE_MAX_FRAME_SIZE: Final[int] = 20

#: Magic prefix for full Tuya BLE application frames
BLE_FRAME_MAGIC: Final[bytes] = b"\x55\xaa"

#: Protocol version byte used in frames (WiFi+BLE combo devices)
BLE_PROTOCOL_VERSION: Final[int] = 0x04

#: Application frame header: magic(2) + version(1) + cmd(1) + seq(2) + len(2) = 10 bytes
_FRAME_HEADER_FMT: Final[str] = ">2sBBHH"  # big-endian
_FRAME_HEADER_SIZE: Final[int] = struct.calcsize(_FRAME_HEADER_FMT)  # 8 bytes

#: Session key size in bytes
BLE_SESSION_KEY_SIZE: Final[int] = 16

#: Random nonce size exchanged in handshake
BLE_NONCE_SIZE: Final[int] = 16

# ── Command codes ────────────────────────────────────────────────────────────

#: Controller → Device: initiate handshake, carries controller nonce
CMD_HANDSHAKE: Final[int] = 0x00

#: Device → Controller: handshake response, carries device nonce
CMD_HANDSHAKE_RESP: Final[int] = 0x00  # Same code, direction distinguishes

#: Controller → Device: WiFi credentials + activation token
CMD_WIFI_CONFIG: Final[int] = 0x01

#: Device → Controller: WiFi config ACK
CMD_WIFI_CONFIG_RESP: Final[int] = 0x02

#: Device → Controller: pairing succeeded (device connected to WiFi + activated)
CMD_PAIR_SUCCESS: Final[int] = 0x04

#: Device → Controller: pairing failed
CMD_PAIR_FAIL: Final[int] = 0x05


# ── CRC ─────────────────────────────────────────────────────────────────────


def crc16_modbus(data: bytes) -> int:
    """Compute CRC-16/MODBUS checksum.

    Uses polynomial 0x8005, initial value 0xFFFF, reflected
    input/output, no final XOR — the standard Modbus CRC variant used
    by Tuya BLE frames.

    Args:
        data: Raw bytes to checksum.

    Returns:
        16-bit CRC integer (0-65535).
    """
    crc: int = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


# ── Frame dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BleFrame:
    """A single Tuya BLE application-layer frame.

    Application frames are larger than the BLE ATT MTU (20 bytes) and are
    therefore split into transport-layer *chunks* before being written to
    the Write characteristic.  Use :func:`build_provision_frames` to get
    the ready-to-send chunk list for a full provisioning exchange.

    Attributes:
        seq:      Monotonically incrementing sequence number (0-65535).
        cmd:      Command byte (see ``CMD_*`` constants).
        payload:  Command-specific binary payload (0-65535 bytes).
    """

    seq: int
    cmd: int
    payload: bytes = field(default=b"")

    def encode(self) -> bytes:
        """Encode the frame to a complete byte string including CRC.

        The format is::

            [magic 2B][version 1B][cmd 1B][seq 2B BE][payload_len 2B BE]
            [payload nB][crc 2B LE]

        Returns:
            Encoded frame bytes.
        """
        header = struct.pack(
            _FRAME_HEADER_FMT,
            BLE_FRAME_MAGIC,
            BLE_PROTOCOL_VERSION,
            self.cmd,
            self.seq,
            len(self.payload),
        )
        body = header + self.payload
        crc = crc16_modbus(body)
        return body + struct.pack("<H", crc)

    @classmethod
    def decode(cls, data: bytes) -> BleFrame:
        """Decode a frame from reassembled bytes.

        Args:
            data: Complete frame bytes (magic through CRC inclusive).

        Returns:
            Decoded :class:`BleFrame`.

        Raises:
            :exc:`~tuya_cloudless.exceptions.PairingError`: If the data
                is too short, has wrong magic, or fails CRC validation.
        """
        min_size = _FRAME_HEADER_SIZE + 2  # header + crc
        if len(data) < min_size:
            raise PairingError(f"BLE frame too short: {len(data)} bytes (minimum {min_size})")

        magic = data[:2]
        if magic != BLE_FRAME_MAGIC:
            raise PairingError(f"BLE frame bad magic: expected 0x55AA, got 0x{magic.hex().upper()}")

        _magic, _version, cmd, seq, payload_len = struct.unpack_from(_FRAME_HEADER_FMT, data)

        frame_end = _FRAME_HEADER_SIZE + payload_len
        if len(data) < frame_end + 2:
            raise PairingError(f"BLE frame truncated: need {frame_end + 2} bytes, got {len(data)}")

        payload = data[_FRAME_HEADER_SIZE:frame_end]
        received_crc = struct.unpack_from("<H", data, frame_end)[0]
        computed_crc = crc16_modbus(data[:frame_end])

        import hmac as _hmac_mod

        if not _hmac_mod.compare_digest(
            struct.pack("<H", received_crc), struct.pack("<H", computed_crc)
        ):
            raise PairingError(
                f"BLE frame CRC mismatch: expected 0x{computed_crc:04X}, got 0x{received_crc:04X}"
            )

        return cls(seq=seq, cmd=cmd, payload=payload)


# ── Chunking ─────────────────────────────────────────────────────────────────


def _chunk_frame(frame_bytes: bytes) -> list[bytes]:
    """Split an encoded frame into BLE transport chunks.

    Each chunk has a 2-byte transport header::

        [chunk_no: 1B 0-indexed][total_chunks: 1B][data: up to 18B]

    Total chunk size ≤ 20 bytes = BLE_MAX_FRAME_SIZE.

    Args:
        frame_bytes: Encoded frame (output of :meth:`BleFrame.encode`).

    Returns:
        List of raw chunk bytes ready to write to the BLE characteristic.
    """
    transport_payload_size = BLE_MAX_FRAME_SIZE - 2  # 2 bytes for chunk header
    chunks_data = [
        frame_bytes[i : i + transport_payload_size]
        for i in range(0, len(frame_bytes), transport_payload_size)
    ]
    total = len(chunks_data)
    if total > 255:
        raise PairingError(
            f"BLE frame too large to chunk: {len(frame_bytes)} bytes produces "
            f"{total} chunks (max 255)"
        )
    return [bytes([idx, total]) + chunk for idx, chunk in enumerate(chunks_data)]


def _reassemble_chunks(chunks: list[bytes]) -> bytes:
    """Reassemble transport chunks into a single frame byte string.

    Args:
        chunks: List of raw chunks received from the notify characteristic
                (order matters; first byte is chunk index).

    Returns:
        Reassembled frame bytes suitable for :meth:`BleFrame.decode`.

    Raises:
        :exc:`~tuya_cloudless.exceptions.PairingError`: If chunks are
            out of order, missing, or the total count is inconsistent.
    """
    if not chunks:
        raise PairingError("Cannot reassemble empty chunk list")

    if len(chunks[0]) < 2:
        raise PairingError("BLE chunk too short — missing chunk index or total bytes")

    total = chunks[0][1]
    if len(chunks) != total:
        raise PairingError(f"Incomplete BLE frame: expected {total} chunks, got {len(chunks)}")
    # Verify all chunks agree on the total — a peripheral sending inconsistent
    # total bytes would otherwise smuggle a frame through the length check.
    if any(c[1] != total for c in chunks):
        raise PairingError("BLE chunks have inconsistent total_chunks values")

    ordered = sorted(chunks, key=lambda c: c[0])
    if len(ordered) != len({c[0] for c in ordered}):
        raise PairingError("BLE chunk list contains duplicate chunk indices")
    for idx, chunk in enumerate(ordered):
        if chunk[0] != idx:
            raise PairingError(f"BLE chunk sequence gap: expected index {idx}, got {chunk[0]}")

    return b"".join(c[2:] for c in ordered)


# ── ProvisionPayload ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProvisionPayload:
    """WiFi provisioning data sent to a Tuya device over BLE.

    After receiving this payload the device will:
      1. Connect to ``ssid`` using ``password``.
      2. POST ``token`` to ``activator_url`` to receive its ``local_key``.

    Attributes:
        ssid:           WiFi network name (SSID).
        password:       WiFi network password (may be empty for open networks).
        token:          Random string used to correlate the activation request;
                        generated by :meth:`generate_token`.
        activator_url:  HTTP URL of the fake-cloud activation endpoint
                        (e.g. ``http://192.168.1.10:8099``).
        region:         Two-letter region code sent to the device (default ``az``
                        = global/generic; unused when a custom activator is set).
    """

    ssid: str
    password: str
    token: str
    activator_url: str
    region: str = "az"

    @staticmethod
    def generate_token() -> str:
        """Generate a cryptographically random 16-character hex token.

        Returns:
            Random hex string (32 hex digits = 16 bytes of entropy).
        """
        return secrets.token_hex(16)

    def to_bytes(self) -> bytes:
        """Encode the payload as compact JSON bytes for BLE transmission.

        Returns:
            UTF-8 encoded JSON with short keys to minimise BLE frame count.
        """
        doc = {
            "s": self.ssid,
            "p": self.password,
            "t": self.token,
            "r": self.region,
            "activator": self.activator_url,
        }
        return json.dumps(doc, separators=(",", ":")).encode()


# ── High-level frame builders ─────────────────────────────────────────────────


def build_provision_frames(
    payload: ProvisionPayload,
    seq: int = 1,
    *,
    session_key: bytes | None = None,
) -> list[bytes]:
    """Build the BLE chunk list for a complete WiFi provisioning exchange.

    Encodes the WiFi config into a :class:`BleFrame` (CMD_WIFI_CONFIG)
    and splits it into BLE transport chunks.  When ``session_key`` is provided
    the payload bytes are AES-128-ECB encrypted before encoding, as required
    by the Tuya BLE provisioning protocol.

    Args:
        payload:     Fully populated :class:`ProvisionPayload`.
        seq:         Sequence number for the frame (default 1).
        session_key: 16-byte session key from :func:`derive_session_key`.
                     Must be provided for production use — omitting it sends
                     the WiFi credentials in cleartext over BLE.

    Returns:
        List of raw byte strings, each ≤ 20 bytes, to be written to the
        BLE Write characteristic in order.
    """
    raw_payload = payload.to_bytes()
    if session_key is not None:
        raw_payload = encrypt_ble_payload(session_key, raw_payload)
    frame = BleFrame(seq=seq, cmd=CMD_WIFI_CONFIG, payload=raw_payload)
    return _chunk_frame(frame.encode())


def build_handshake_frame(controller_nonce: bytes | None = None) -> list[bytes]:
    """Build the initial handshake frame sent by the controller.

    The handshake frame carries a random 16-byte nonce.  The device
    responds with its own nonce; both are XOR-combined to derive the
    AES-128-ECB session key used for encrypting subsequent frames.

    Args:
        controller_nonce: Optional 16-byte nonce.  If *None*, a
            cryptographically random nonce is generated.

    Returns:
        List of BLE transport chunks to write to the device.

    Raises:
        :exc:`~tuya_cloudless.exceptions.PairingError`: If ``controller_nonce``
            is supplied but is not exactly 16 bytes.
    """
    if controller_nonce is None:
        controller_nonce = os.urandom(BLE_NONCE_SIZE)

    if len(controller_nonce) != BLE_NONCE_SIZE:
        raise PairingError(
            f"Controller nonce must be {BLE_NONCE_SIZE} bytes, got {len(controller_nonce)}"
        )

    frame = BleFrame(seq=0, cmd=CMD_HANDSHAKE, payload=controller_nonce)
    return _chunk_frame(frame.encode())


def derive_session_key(controller_nonce: bytes, device_nonce: bytes) -> bytes:
    """Derive the AES-128 session key from the two exchanged nonces.

    The session key is the XOR of the controller and device nonces.
    This simple key derivation is used by Tuya BLE provisioning for
    encrypting the WiFi credential frame.

    Args:
        controller_nonce: 16-byte nonce sent by the controller.
        device_nonce:     16-byte nonce received from the device.

    Returns:
        16-byte AES session key.

    Raises:
        :exc:`~tuya_cloudless.exceptions.PairingError`: If either nonce
            is not exactly 16 bytes.
    """
    if len(controller_nonce) != BLE_SESSION_KEY_SIZE:
        raise PairingError(f"Controller nonce must be {BLE_SESSION_KEY_SIZE} bytes")
    if len(device_nonce) != BLE_SESSION_KEY_SIZE:
        raise PairingError(f"Device nonce must be {BLE_SESSION_KEY_SIZE} bytes")
    return bytes(a ^ b for a, b in zip(controller_nonce, device_nonce, strict=True))


def parse_ble_response(chunks: list[bytes]) -> BleFrame:
    """Parse a BLE notify response from raw transport chunks.

    Reassembles chunks and decodes the application frame.

    Args:
        chunks: Raw notify data chunks (one per ``characteristicvaluechanged``
                event, or one per BLE notification callback call).

    Returns:
        Decoded :class:`BleFrame`.

    Raises:
        :exc:`~tuya_cloudless.exceptions.PairingError`: On CRC error,
            bad magic, or incomplete chunks.
    """
    frame_bytes = _reassemble_chunks(chunks)
    return BleFrame.decode(frame_bytes)


# ── BleProvisioner ────────────────────────────────────────────────────────────


class BleProvisioner:
    """Provision a Tuya WiFi+BLE device over Bluetooth LE.

    Uses the *bleak* library (``pip install bleak``) for BLE communication.
    Handles the full provisioning sequence:

      1. Scan for a device advertising the Tuya provisioning service.
      2. GATT connect + characteristic discovery.
      3. Handshake — exchange nonces, derive session key.
      4. Send WiFi credentials + activation token.
      5. Wait for pairing ACK.

    After provisioning completes the device connects to WiFi and calls the
    ``activator_url`` in the :class:`ProvisionPayload` to obtain its
    ``local_key``.

    Example::

        async with BleProvisioner() as p:
            addr = await p.scan(timeout=10.0)
            payload = ProvisionPayload(
                ssid="MyWiFi",
                password="secret",  # pragma: allowlist secret
                token=ProvisionPayload.generate_token(),
                activator_url="http://192.168.1.10:8099",
            )
            await p.provision(addr, payload)

    Note:
        *bleak* must be installed.  Import errors are deferred to
        :meth:`scan` / :meth:`provision` to avoid breaking installations
        that do not use BLE.
    """

    def __init__(self, scan_timeout: float = 10.0, connect_timeout: float = 15.0) -> None:
        """Initialise the provisioner.

        Args:
            scan_timeout:    Maximum seconds to scan for a provisionable device.
            connect_timeout: Maximum seconds to wait for GATT connection.
        """
        self._scan_timeout = scan_timeout
        self._connect_timeout = connect_timeout
        self._client: object | None = None  # bleak.BleakClient instance

    async def __aenter__(self) -> BleProvisioner:
        """Return self for use as async context manager."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Disconnect on context manager exit."""
        await self.disconnect()

    async def scan(self, timeout: float | None = None) -> str:
        """Scan for a Tuya device in BLE provisioning mode.

        Args:
            timeout: Scan duration in seconds.  Defaults to
                :attr:`scan_timeout`.

        Returns:
            BLE address string of the first discovered Tuya device.

        Raises:
            :exc:`~tuya_cloudless.exceptions.PairingError`: If bleak is
                not installed or no device is found within *timeout*.
        """
        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise PairingError(
                "bleak is required for BLE provisioning — install it with 'pip install bleak'"
            ) from exc

        scan_time = timeout if timeout is not None else self._scan_timeout
        device = await BleakScanner.find_device_by_filter(
            lambda d, _adv: (
                BLE_SERVICE_UUID in (str(s).lower() for s in (_adv.service_uuids or []))
            ),
            timeout=scan_time,
        )

        if device is None:
            raise PairingError(
                f"No Tuya BLE device found after {scan_time:.0f}s scan. "
                "Ensure the device is in provisioning mode (factory reset or "
                "long-press the pair button until indicator flashes rapidly)."
            )

        return str(device.address)

    async def provision(
        self,
        address: str,
        payload: ProvisionPayload,
        pair_timeout: float = 30.0,
    ) -> None:
        """Connect to a device and send WiFi credentials.

        Full provisioning sequence:
          1. GATT connect.
          2. Subscribe to notify characteristic.
          3. Send handshake frame + wait for device nonce.
          4. Encrypt WiFi config frame with derived session key.
          5. Send WiFi config frame.
          6. Wait for :const:`CMD_PAIR_SUCCESS` ACK.

        Args:
            address:      BLE address returned by :meth:`scan`.
            payload:      WiFi config + activation token.
            pair_timeout: Maximum seconds to wait for pairing ACK from
                          the device (after sending credentials).

        Raises:
            :exc:`~tuya_cloudless.exceptions.PairingError`: On any BLE
                or protocol error.
        """
        import asyncio

        try:
            from bleak import BleakClient
            from bleak.backends.characteristic import BleakGATTCharacteristic
        except ImportError as exc:
            raise PairingError(
                "bleak is required for BLE provisioning — install it with 'pip install bleak'"
            ) from exc

        recv_chunks: list[bytes] = []
        notify_event: asyncio.Event = asyncio.Event()

        def _on_notify(_char: BleakGATTCharacteristic, data: bytearray) -> None:
            if len(data) < 2:  # malformed chunk — need at least chunk_no and total bytes
                return
            # Cap to prevent unbounded memory growth from a malicious peripheral
            # sending chunks without ever setting the last-chunk flag.
            if len(recv_chunks) >= 512:
                recv_chunks.clear()
                notify_event.clear()  # Reset so the next frame's last chunk sets it
                # R36-5: Only restart accumulation on chunk_no == 0 (first chunk of
                # a new frame).  If the incoming chunk is mid-frame (chunk_no > 0),
                # the preceding chunks were discarded by the cap so this chunk belongs
                # to an already-broken frame — drop it and wait for a fresh start.
                if data[0] != 0:
                    return
            recv_chunks.append(bytes(data))
            if data[0] + 1 == data[1]:  # last chunk (chunk_no + 1 == total)
                notify_event.set()

        try:
            async with BleakClient(address, timeout=self._connect_timeout) as client:
                self._client = client

                await client.start_notify(BLE_NOTIFY_CHAR_UUID, _on_notify)

                # Step 1: Handshake — send controller nonce
                controller_nonce = os.urandom(BLE_NONCE_SIZE)
                for chunk in build_handshake_frame(controller_nonce):
                    await client.write_gatt_char(BLE_WRITE_CHAR_UUID, chunk, response=False)

                # Wait for device nonce response
                try:
                    await asyncio.wait_for(notify_event.wait(), timeout=10.0)
                except TimeoutError as exc:
                    raise PairingError("Timeout waiting for BLE handshake response") from exc

                resp = parse_ble_response(recv_chunks)
                if resp.cmd != CMD_HANDSHAKE_RESP or len(resp.payload) < BLE_NONCE_SIZE:
                    raise PairingError(
                        f"Unexpected handshake response: cmd=0x{resp.cmd:02X}, "
                        f"payload_len={len(resp.payload)}"
                    )

                device_nonce = resp.payload[:BLE_NONCE_SIZE]
                # R35-6: Reject all-zeros nonce — XOR key derivation with a zero
                # nonce produces session_key == controller_nonce, which a rogue
                # device (knowing only the public nonce) could exploit.
                if device_nonce == b"\x00" * BLE_NONCE_SIZE:
                    raise PairingError(
                        "Device returned invalid all-zeros nonce — potential rogue device"
                    )
                session_key = derive_session_key(controller_nonce, device_nonce)
                # Clear event BEFORE chunks to close the TOCTOU window where a
                # spurious notification between the two clears would leave the
                # event set with an empty recv_chunks list.
                notify_event.clear()
                recv_chunks.clear()

                # Step 2: Send WiFi config, encrypted with the session key
                for chunk in build_provision_frames(payload, seq=1, session_key=session_key):
                    await client.write_gatt_char(BLE_WRITE_CHAR_UUID, chunk, response=False)

                # Wait for ACK
                try:
                    await asyncio.wait_for(notify_event.wait(), timeout=pair_timeout)
                except TimeoutError as exc:
                    raise PairingError(
                        f"Timeout waiting for WiFi config ACK after {pair_timeout:.0f}s"
                    ) from exc

                ack = parse_ble_response(recv_chunks)
                if ack.cmd == CMD_PAIR_FAIL:
                    raise PairingError("Device rejected WiFi config — check SSID and password")
                if ack.cmd not in (CMD_WIFI_CONFIG_RESP, CMD_PAIR_SUCCESS):
                    raise PairingError(f"Unexpected ACK command: 0x{ack.cmd:02X}")

        except PairingError:
            raise
        except (OSError, RuntimeError) as exc:
            # bleak raises OSError for I/O failures and RuntimeError for
            # protocol/GATT errors; convert both to PairingError.
            raise PairingError(f"BLE provisioning failed: {exc}") from exc
        finally:
            self._client = None

    async def disconnect(self) -> None:
        """Disconnect from the BLE device if currently connected."""
        import contextlib

        client = self._client
        if client is not None:
            with contextlib.suppress(OSError, RuntimeError):
                from bleak import BleakClient

                if isinstance(client, BleakClient) and client.is_connected:
                    await client.disconnect()
            self._client = None
