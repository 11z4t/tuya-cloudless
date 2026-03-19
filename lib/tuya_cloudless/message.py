"""Tuya WiFi 0x55AA message format encoder/decoder.

Implements the complete Tuya Local protocol message framing:

Wire format::

    ┌──────────┬──────────┬──────────┬──────────┬─────────┬──────────┬──────────┐
    │ PREFIX   │ SEQ_NO   │ CMD_TYPE │ PAY_LEN  │ PAYLOAD │ CHECKSUM │ SUFFIX   │
    │ 4 bytes  │ 4 bytes  │ 4 bytes  │ 4 bytes  │ N bytes │ 4 or 32B │ 4 bytes  │
    │ 000055AA │ uint32BE │ uint32BE │ uint32BE │ (var)   │ CRC/HMAC │ 0000AA55 │
    └──────────┴──────────┴──────────┴──────────┴─────────┴──────────┴──────────┘

Checksum coverage: from PREFIX through end of PAYLOAD (inclusive).
- v3.1 / v3.3: CRC32 (4 bytes)
- v3.4 / v3.5: HMAC-SHA256 (32 bytes)

The ``payload_length`` field counts PAYLOAD + CHECKSUM + SUFFIX combined
(this is the Tuya convention, NOT just the payload size).

This module handles ONLY message framing (encode/decode/reassembly).
Payload encryption/decryption is delegated to ``crypto.py`` (Rule S3).
Network I/O is handled by ``protocol.py`` (Rule S2).
"""

from __future__ import annotations

import hmac as _hmac_mod
import json
import logging
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .const import MAX_PAYLOAD_SIZE
from .crypto import (
    ProtocolVersion,
    crc32_bytes,
    hmac_sha256,
)
from .exceptions import InvalidMessageError, ProtocolError

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wire format constants
# ---------------------------------------------------------------------------

PREFIX = 0x000055AA
SUFFIX = 0x0000AA55

PREFIX_BYTES = struct.pack(">I", PREFIX)
SUFFIX_BYTES = struct.pack(">I", SUFFIX)

# Header: prefix(4) + seq(4) + cmd(4) + payload_len(4)
HEADER_SIZE = 16

# Checksum sizes by protocol version
CRC32_SIZE = 4
HMAC_SIZE = 32

SUFFIX_SIZE = 4

# Minimum message size: header + CRC32 + suffix (v3.1/v3.3, empty payload)
MIN_MESSAGE_SIZE_V33 = HEADER_SIZE + CRC32_SIZE + SUFFIX_SIZE
# Minimum message size for v3.4+ (HMAC instead of CRC32)
MIN_MESSAGE_SIZE_V34 = HEADER_SIZE + HMAC_SIZE + SUFFIX_SIZE

# Absolute minimum (v3.1/v3.3)
MIN_MESSAGE_SIZE = MIN_MESSAGE_SIZE_V33


# ---------------------------------------------------------------------------
# Command types
# ---------------------------------------------------------------------------


class CommandType(IntEnum):
    """Tuya Local protocol command types.

    Each command type represents a specific operation in the Tuya protocol.
    Not all command types are available on all protocol versions.
    """

    # Device discovery (UDP broadcast)
    UDP = 0x01
    AP_CONFIG = 0x02
    ACTIVE = 0x03

    # Session management (v3.4+: key negotiation)
    SESS_KEY_NEG = 0x04
    SESS_KEY_NEG_RESP = 0x05

    DEVICE_REMOVED = 0x06

    # Core device control
    CONTROL = 0x07
    STATUS = 0x08
    HEART_BEAT = 0x09
    DP_QUERY = 0x0A

    # Extended (v3.2+)
    QUERY_WIFI = 0x0B
    TOKEN_BIND = 0x0C
    CONTROL_NEW = 0x0D
    ENABLE_WIFI = 0x0E
    DP_QUERY_NEW = 0x10
    SCENE_EXECUTE = 0x11
    UPDATE_DPS = 0x12
    LAN_GW_ACTIVE = 0x13

    # LAN extensions
    LAN_SUB_DEV_REQUEST = 0x14
    LAN_DELETE_SUB_DEV = 0x15
    LAN_REPORT_SUB_DEV = 0x16
    LAN_SCENE = 0x17
    LAN_PUBLISH_CLOUD_CONFIG = 0x18
    LAN_PUBLISH_APP_CONFIG = 0x19
    LAN_EXPORT_APP_CONFIG = 0x1A
    LAN_PUBLISH_SCENE_PANEL = 0x1B
    LAN_REMOVE_GW = 0x1C
    LAN_CHECK_GW_UPDATE = 0x1D
    LAN_GW_UPDATE = 0x1E
    LAN_SET_GW_CHANNEL = 0x1F

    # v3.5 extended
    BIND_V35 = 0x24
    UNBIND_V35 = 0x25

    @classmethod
    def from_int(cls, value: int) -> CommandType | int:
        """Convert integer to CommandType, returning raw int for unknown values.

        Unknown command codes are returned as plain ints rather than raising
        ``InvalidMessageError``.  This prevents new Tuya firmware that uses
        previously unseen command codes from being counted as decode errors and
        triggering a permanent reconnect loop in the coordinator.

        Args:
            value: Integer command type from wire.

        Returns:
            Matching :class:`CommandType` member, or the raw ``int`` if unknown.
        """
        try:
            return cls(value)
        except ValueError:
            return value


# ---------------------------------------------------------------------------
# Message data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TuyaMessage:
    """A single decoded Tuya 0x55AA protocol message.

    This is the unified frame/message model for the Tuya LAN protocol.
    :class:`~tuya_cloudless.protocol.TuyaFrame` is a backward-compatible
    alias for this class.

    Attributes:
        sequence: Message sequence number (monotonically increasing).
        command: Command type (use :class:`CommandType` constants).
        payload: Payload bytes. Raw/encrypted when returned by
            :func:`decode_message`; decrypted when returned by
            :func:`~tuya_cloudless.protocol.decode_frame`.
        version: Protocol version string (e.g. ``"3.3"``). Empty string
            when not applicable.
        raw: Original wire-format bytes. Set by decode functions; empty
            when constructing outbound messages.
    """

    sequence: int
    command: int  # CommandType (IntEnum) values are accepted
    payload: bytes
    version: str = ""
    raw: bytes = field(default=b"", repr=False)

    @property
    def payload_length(self) -> int:
        """Length of the payload in bytes."""
        return len(self.payload)

    @property
    def dps(self) -> dict[str, Any]:
        """Decode ``payload`` as a JSON DPS dictionary.

        Returns:
            DPS dict (may be empty if payload is empty).

        Raises:
            ProtocolError: If the payload is not valid JSON.
        """
        if not self.payload:
            return {}
        try:
            data = json.loads(self.payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"Payload is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ProtocolError("DPS payload is not a JSON object")
        return data


# Backward-compatible alias: TuyaFrame is the same class as TuyaMessage.
# Import from tuya_cloudless.protocol for the canonical name.
TuyaFrame = TuyaMessage


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _checksum_size(version: ProtocolVersion) -> int:
    """Return checksum size in bytes for the given protocol version."""
    if version in (ProtocolVersion.V34, ProtocolVersion.V35):
        return HMAC_SIZE
    return CRC32_SIZE


def encode_message(
    msg: TuyaMessage,
    version: ProtocolVersion,
    local_key: bytes | None = None,
) -> bytes:
    """Encode a TuyaMessage into wire-format bytes.

    Constructs the complete 0x55AA framed message with header, payload,
    checksum (CRC32 or HMAC-SHA256), and suffix.

    Args:
        msg: Message to encode.
        version: Protocol version (determines checksum type).
        local_key: 16-byte key for HMAC-SHA256 (required for v3.4+).

    Returns:
        Complete wire-format bytes ready for TCP transmission.

    Raises:
        ProtocolError: If local_key is missing for v3.4+ or encoding fails.
    """
    chk_size = _checksum_size(version)

    # payload_length field = payload + checksum + suffix
    payload_len_field = len(msg.payload) + chk_size + SUFFIX_SIZE

    # Header: prefix + seq + cmd + payload_len
    header = struct.pack(
        ">IIII",
        PREFIX,
        msg.sequence,
        msg.command,
        payload_len_field,
    )

    # Data to checksum: header + payload
    checksum_input = header + msg.payload

    if version in (ProtocolVersion.V34, ProtocolVersion.V35):
        if local_key is None:
            msg_str = f"HMAC key required for protocol version {version}"
            raise ProtocolError(msg_str)
        checksum = hmac_sha256(local_key, checksum_input)
    else:
        checksum = crc32_bytes(checksum_input)

    return header + msg.payload + checksum + SUFFIX_BYTES


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _detect_version_from_size(
    payload_len_field: int,
    raw_payload_size: int,
) -> ProtocolVersion | None:
    """Heuristic to detect protocol version from payload length field.

    The payload_length field includes payload + checksum + suffix.
    By comparing with actual raw_payload_size we can infer CRC32 vs HMAC.

    Returns None if ambiguous (caller should use explicit version).
    """
    # payload_len_field = raw_payload + checksum + suffix
    if payload_len_field == raw_payload_size + CRC32_SIZE + SUFFIX_SIZE:
        return ProtocolVersion.V33  # Could be v3.1 too, but same checksum
    if payload_len_field == raw_payload_size + HMAC_SIZE + SUFFIX_SIZE:
        return ProtocolVersion.V34
    return None


def decode_message(
    data: bytes,
    version: ProtocolVersion,
    local_key: bytes | None = None,
) -> TuyaMessage:
    """Decode a single complete 0x55AA wire-format message.

    Validates prefix, suffix, and checksum (CRC32 or HMAC-SHA256).

    Args:
        data: Complete wire-format bytes for one message.
        version: Protocol version (determines checksum type).
        local_key: 16-byte key for HMAC verification (required for v3.4+).

    Returns:
        Decoded TuyaMessage with raw payload (still encrypted if applicable).

    Raises:
        InvalidMessageError: If message is malformed, too short, has wrong
            prefix/suffix, or fails checksum verification.
        ProtocolError: If local_key is missing for v3.4+.
    """
    chk_size = _checksum_size(version)
    min_size = HEADER_SIZE + chk_size + SUFFIX_SIZE

    if len(data) < min_size:
        msg = f"Message too short: {len(data)} < {min_size}"
        raise InvalidMessageError(msg)

    # Validate prefix
    prefix_val = struct.unpack(">I", data[:4])[0]
    if prefix_val != PREFIX:
        msg = f"Invalid prefix: 0x{prefix_val:08X} (expected 0x{PREFIX:08X})"
        raise InvalidMessageError(msg)

    # Parse header
    seq, cmd_int, payload_len_field = struct.unpack(">III", data[4:16])

    # payload_len_field = payload + checksum + suffix
    payload_size = payload_len_field - chk_size - SUFFIX_SIZE
    if payload_size < 0:
        msg = f"payload_len_field {payload_len_field} too small for checksum+suffix overhead"
        raise InvalidMessageError(msg)
    if payload_size > MAX_PAYLOAD_SIZE:
        msg = f"payload_size {payload_size} exceeds MAX_PAYLOAD_SIZE {MAX_PAYLOAD_SIZE}"
        raise InvalidMessageError(msg)
    expected_total_bytes = HEADER_SIZE + payload_size + chk_size + SUFFIX_SIZE

    if len(data) < expected_total_bytes:
        msg = f"Message truncated: {len(data)} < {expected_total_bytes}"
        raise InvalidMessageError(msg)

    # Extract payload
    payload = data[HEADER_SIZE : HEADER_SIZE + payload_size]

    # Extract checksum
    chk_offset = HEADER_SIZE + payload_size
    received_checksum = data[chk_offset : chk_offset + chk_size]

    # Validate suffix
    suffix_offset = chk_offset + chk_size
    suffix_val = struct.unpack(">I", data[suffix_offset : suffix_offset + SUFFIX_SIZE])[0]
    if suffix_val != SUFFIX:
        msg = f"Invalid suffix: 0x{suffix_val:08X} (expected 0x{SUFFIX:08X})"
        raise InvalidMessageError(msg)

    # Verify checksum
    checksum_input = data[: HEADER_SIZE + payload_size]

    if version in (ProtocolVersion.V34, ProtocolVersion.V35):
        if local_key is None:
            msg_str = f"HMAC key required for protocol version {version}"
            raise ProtocolError(msg_str)
        expected_checksum = hmac_sha256(local_key, checksum_input)
        if not hmac_compare(received_checksum, expected_checksum):
            msg = "HMAC-SHA256 verification failed"
            raise InvalidMessageError(msg)
    else:
        expected_checksum = crc32_bytes(checksum_input)
        # Use constant-time comparison per stated security principle in CLAUDE.md:
        # "All MAC/tag verification uses constant-time comparison."
        # CRC32 has no secret to protect via timing, but we honour the invariant.
        if not hmac_compare(received_checksum, expected_checksum):
            msg = (
                f"CRC32 mismatch: received 0x{received_checksum.hex()}, "
                f"expected 0x{expected_checksum.hex()}"
            )
            raise InvalidMessageError(msg)

    command = CommandType.from_int(cmd_int)

    return TuyaMessage(
        sequence=seq,
        command=command,
        payload=payload,
        version=version,
        raw=data,
    )


def hmac_compare(a: bytes, b: bytes) -> bool:
    """Constant-time comparison for HMAC digests.

    Prevents timing side-channel attacks on HMAC verification.

    Args:
        a: First digest.
        b: Second digest.

    Returns:
        True if digests are equal.
    """
    return _hmac_mod.compare_digest(a, b)


# ---------------------------------------------------------------------------
# TCP stream reassembly
# ---------------------------------------------------------------------------


class MessageBuffer:
    """Reassembles Tuya messages from a TCP byte stream.

    TCP is a stream protocol — messages may arrive fragmented across
    multiple ``recv()`` calls, or multiple messages may arrive in a
    single ``recv()``. This class buffers incoming bytes and yields
    complete messages as they become available.

    The buffer enforces a maximum size to prevent memory exhaustion
    from malformed or malicious streams.

    Usage::

        buf = MessageBuffer(version=ProtocolVersion.V33)
        buf.feed(data_from_recv_1)
        for msg in buf.messages():
            handle(msg)
        buf.feed(data_from_recv_2)
        for msg in buf.messages():
            handle(msg)
    """

    # Maximum buffer size (256 KB) — prevents memory exhaustion
    MAX_BUFFER_SIZE = 256 * 1024

    def __init__(
        self,
        version: ProtocolVersion,
        local_key: bytes | None = None,
    ) -> None:
        """Initialize the message buffer.

        Args:
            version: Protocol version (determines checksum type).
            local_key: 16-byte key for HMAC (required for v3.4+).
        """
        self._buffer = bytearray()
        self._version = version
        self._local_key = local_key
        self._chk_size = _checksum_size(version)
        self._min_msg = HEADER_SIZE + self._chk_size + SUFFIX_SIZE
        self._error_count: int = 0

    def feed(self, data: bytes) -> None:
        """Append received bytes to the internal buffer.

        Args:
            data: Raw bytes from TCP recv().

        Raises:
            ProtocolError: If buffer exceeds MAX_BUFFER_SIZE.
        """
        if len(self._buffer) + len(data) > self.MAX_BUFFER_SIZE:
            msg = (
                f"Message buffer overflow: {len(self._buffer) + len(data)} > {self.MAX_BUFFER_SIZE}"
            )
            raise ProtocolError(msg)
        self._buffer.extend(data)

    def _find_prefix(self) -> int | None:
        """Find the offset of the next 0x55AA prefix in the buffer.

        Discards any bytes before the first valid prefix (handles
        stream corruption or mid-stream connection).

        Returns:
            Offset of prefix, or None if not found.
        """
        idx = self._buffer.find(PREFIX_BYTES)
        if idx < 0:
            # No prefix found — discard all but last 3 bytes
            # (a partial prefix could straddle the boundary)
            keep = min(len(self._buffer), len(PREFIX_BYTES) - 1)
            del self._buffer[: len(self._buffer) - keep]
            return None
        if idx > 0:
            # Discard garbage before prefix
            _LOGGER.debug("Discarding %d bytes before 0x55AA prefix", idx)
            del self._buffer[:idx]
        return 0

    def _try_extract(self) -> TuyaMessage | None:
        """Try to extract one complete message from the buffer.

        Returns:
            Decoded message, or None if buffer doesn't contain a complete one.
        """
        prefix_offset = self._find_prefix()
        if prefix_offset is None:
            return None

        # Need at least a full header to read payload_len
        if len(self._buffer) < HEADER_SIZE:
            return None

        # Read payload_len field to determine total message size
        payload_len_field = struct.unpack(">I", self._buffer[12:16])[0]
        payload_size = payload_len_field - self._chk_size - SUFFIX_SIZE
        if payload_size < 0:
            # Invalid payload_len — discard this prefix and retry
            _LOGGER.debug("Invalid payload_len_field: %d, discarding prefix", payload_len_field)
            del self._buffer[:4]
            return None

        # Reject implausibly large frames to prevent memory amplification from a
        # crafted device that advertises a huge payload in the length field.
        # (Consistent with the same guard in split_frames in protocol.py.)
        if payload_size > MAX_PAYLOAD_SIZE:
            _LOGGER.debug(
                "Frame payload_size %d exceeds MAX_PAYLOAD_SIZE %d, discarding prefix",
                payload_size,
                MAX_PAYLOAD_SIZE,
            )
            del self._buffer[:4]
            return None

        total_size = HEADER_SIZE + payload_size + self._chk_size + SUFFIX_SIZE

        # Wait for more data if message is incomplete
        if len(self._buffer) < total_size:
            return None

        # Extract complete message bytes
        msg_bytes = bytes(self._buffer[:total_size])
        del self._buffer[:total_size]

        # Let decode errors propagate — callers decide whether to count/reconnect.
        return decode_message(msg_bytes, self._version, self._local_key)

    def messages(self) -> list[TuyaMessage]:
        """Extract all complete messages currently in the buffer.

        Decode errors for individual frames are counted (see :meth:`pop_error_count`)
        but do not abort extraction — subsequent frames are still processed.

        Returns:
            List of successfully decoded messages (may be empty).
        """
        result: list[TuyaMessage] = []
        prev_buf_len = len(self._buffer) + 1  # sentinel larger than any real length
        while True:
            cur_buf_len = len(self._buffer)
            if cur_buf_len >= prev_buf_len:
                # Buffer did not shrink — _try_extract made no progress.
                # Break to avoid a tight infinite loop that starves the event loop.
                _LOGGER.warning(
                    "MessageBuffer: extraction loop made no progress (%d bytes remain); "
                    "discarding buffer to prevent stall",
                    cur_buf_len,
                )
                # Count the stall as an error so the coordinator's consecutive-error
                # reconnect threshold is correctly triggered.
                self._error_count += 1
                self._buffer.clear()
                break
            prev_buf_len = cur_buf_len
            try:
                msg = self._try_extract()
            except (InvalidMessageError, ProtocolError) as exc:
                _LOGGER.warning("Failed to decode message: %s", exc)
                self._error_count += 1
                # The bad frame bytes were consumed; try the next frame.
                continue
            if msg is None:
                break
            result.append(msg)
        return result

    def pop_error_count(self) -> int:
        """Return and reset the number of frame-decode errors since the last call.

        The coordinator uses this to increment its consecutive-error counter and
        decide whether to force a reconnect.
        """
        n = self._error_count
        self._error_count = 0
        return n

    def clear(self) -> None:
        """Discard all buffered data.

        Call this on disconnect or protocol reset.
        """
        self._buffer.clear()

    @property
    def pending_bytes(self) -> int:
        """Number of bytes waiting in the buffer."""
        return len(self._buffer)


# ---------------------------------------------------------------------------
# Convenience builders for common commands
# ---------------------------------------------------------------------------


def build_heartbeat(sequence: int) -> TuyaMessage:
    """Build a HEART_BEAT request message.

    Args:
        sequence: Message sequence number.

    Returns:
        TuyaMessage with empty payload.
    """
    return TuyaMessage(sequence=sequence, command=CommandType.HEART_BEAT, payload=b"")


def build_dp_query(sequence: int, dps_ids: list[int] | None = None) -> TuyaMessage:
    """Build a DP_QUERY request message.

    Args:
        sequence: Message sequence number.
        dps_ids: Optional list of DP IDs to query. If None, queries all.

    Returns:
        TuyaMessage with JSON-encoded DPS query payload.
    """
    if dps_ids is not None:
        payload_dict = {"dps": {str(dp): None for dp in dps_ids}}
    else:
        payload_dict = {"dps": {}}
    payload = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    return TuyaMessage(sequence=sequence, command=CommandType.DP_QUERY, payload=payload)


def build_control(sequence: int, dps: dict[str, object]) -> TuyaMessage:
    """Build a CONTROL command message.

    Args:
        sequence: Message sequence number.
        dps: Data points to set (e.g., ``{"1": True, "2": 50}``).

    Returns:
        TuyaMessage with JSON-encoded DPS control payload.
    """
    payload_dict = {"dps": dps}
    payload = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    return TuyaMessage(sequence=sequence, command=CommandType.CONTROL, payload=payload)


def build_status_request(sequence: int) -> TuyaMessage:
    """Build a STATUS request message.

    Args:
        sequence: Message sequence number.

    Returns:
        TuyaMessage with empty payload.
    """
    return TuyaMessage(sequence=sequence, command=CommandType.STATUS, payload=b"")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "HEADER_SIZE",
    "PREFIX",
    "SUFFIX",
    "CommandType",
    "MessageBuffer",
    "TuyaFrame",
    "TuyaMessage",
    "build_control",
    "build_dp_query",
    "build_heartbeat",
    "build_status_request",
    "decode_message",
    "encode_message",
]
