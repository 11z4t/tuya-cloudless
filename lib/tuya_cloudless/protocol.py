"""Tuya LAN protocol v3.1-3.5 - frame encode/decode.

Frame wire format (all multi-byte fields are big-endian):

  ┌─────────┬──────────┬─────────┬──────────┬──────────────┬──────────┬──────────┐
  │ Prefix  │ Sequence │ Command │  Length  │   Payload    │   CRC    │  Suffix  │
  │ 4 bytes │  4 bytes │ 4 bytes │  4 bytes │ Length bytes │  4 bytes │  4 bytes │
  └─────────┴──────────┴─────────┴──────────┴──────────────┴──────────┴──────────┘

  Prefix  = 0x000055AA
  Suffix  = 0x0000AA55
  Length  = len(payload) + 4 (CRC) + 4 (suffix)
  CRC     = CRC-32 over (prefix + sequence + command + length + payload)

For v3.4/3.5:
  The CRC field is replaced by a 4-byte GCM sequence counter; the GCM tag is
  embedded in the encrypted payload envelope (see crypto.py).

DPS (Data Points) are the unit of device state: a JSON dict mapping integer
string keys to typed values (bool, int, str, bytes).
"""

from __future__ import annotations

import json
import struct
from typing import Any

from tuya_cloudless.const import (
    CMD_CONTROL,
    CMD_DP_QUERY,
    CMD_HEARTBEAT,
    CMD_SESS_KEY_NEG_FINISH,
    CMD_SESS_KEY_NEG_START,
    CMD_STATUS,
    FRAME_PREFIX,
    FRAME_SUFFIX,
    MAX_PAYLOAD_SIZE,
    PROTOCOL_31,
    VERSIONS_GCM,
)
from tuya_cloudless.crypto import (
    compute_crc32,
    decrypt_payload,
    encrypt_payload,
    verify_crc32,
)
from tuya_cloudless.exceptions import (
    MalformedPacketError,
    UnsupportedVersionError,
)
from tuya_cloudless.message import TuyaMessage

# TuyaFrame is the canonical name for TuyaMessage in the protocol layer.
# Both names refer to the same class; import from either module.
TuyaFrame = TuyaMessage

__all__ = [
    "TuyaFrame",
    "decode_frame",
    "encode_control",
    "encode_frame",
    "encode_heartbeat",
    "encode_session_key_finish",
    "encode_session_key_start",
    "encode_status_query",
    "encode_status_response",
    "split_frames",
]

# ── Internal constants ────────────────────────────────────────────────────────

_STRUCT_HEADER = struct.Struct(">4sIII")  # prefix(4) + seq(4) + cmd(4) + length(4)
_STRUCT_SUFFIX = struct.Struct(">4s")
_MIN_FRAME_SIZE = _STRUCT_HEADER.size + 4 + 4  # header + CRC + suffix


# ── Encoding ──────────────────────────────────────────────────────────────────


def encode_frame(
    command: int,
    payload: bytes,
    *,
    sequence: int,
    version: str,
    local_key: bytes,
    session_key: bytes | None = None,
) -> bytes:
    """Encode a Tuya LAN frame.

    Args:
        command: Command code (e.g. :data:`.const.CMD_CONTROL`).
        payload: Unencrypted payload bytes (JSON DPS, empty for heartbeat, etc.).
        sequence: Frame sequence number (monotonically increasing).
        version: Protocol version string (e.g. "3.3").
        local_key: 16-byte device local key.
        session_key: ECDH session key (required for v3.4/3.5).

    Returns:
        Complete wire-format frame bytes.

    Raises:
        UnsupportedVersionError: If ``version`` is not in 3.1-3.5.
        CryptoError: On encryption failure.
    """
    _check_version(version)

    if version == PROTOCOL_31:
        # v3.1: payload is plaintext JSON; no encryption for STATUS/DP_QUERY
        encrypted = payload
    else:
        encrypted = encrypt_payload(version, local_key, payload, session_key)

    # length = encrypted_payload + CRC(4) + suffix(4)
    length = len(encrypted) + 8
    header = _STRUCT_HEADER.pack(FRAME_PREFIX, sequence & 0xFFFFFFFF, command & 0xFFFFFFFF, length)
    body = header + encrypted
    crc = compute_crc32(body)
    return body + struct.pack(">I", crc) + FRAME_SUFFIX


def encode_heartbeat(
    *,
    sequence: int,
    version: str,
    local_key: bytes,
    session_key: bytes | None = None,
) -> bytes:
    """Encode a heartbeat (keepalive) frame.

    Args:
        sequence: Frame sequence number.
        version: Protocol version string.
        local_key: Device local key (used for encryption in v3.2+).
        session_key: Active session key for v3.4/v3.5 GCM encryption.
                     Must be provided for v3.4/v3.5 devices.

    Returns:
        Wire-format heartbeat frame.
    """
    return encode_frame(
        CMD_HEARTBEAT,
        b"",
        sequence=sequence,
        version=version,
        local_key=local_key,
        session_key=session_key,
    )


def encode_control(
    dps: dict[str, Any],
    *,
    sequence: int,
    version: str,
    local_key: bytes,
    session_key: bytes | None = None,
) -> bytes:
    """Encode a DPS control command.

    Args:
        dps: Dict mapping DP string IDs to values, e.g. ``{"1": True}``.
        sequence: Frame sequence number.
        version: Protocol version string.
        local_key: Device local key.
        session_key: ECDH session key (v3.4/3.5).

    Returns:
        Wire-format control frame.
    """
    cmd = CMD_CONTROL
    payload = json.dumps({"dps": dps}, separators=(",", ":")).encode()
    return encode_frame(
        cmd,
        payload,
        sequence=sequence,
        version=version,
        local_key=local_key,
        session_key=session_key,
    )


def encode_status_query(
    *,
    sequence: int,
    version: str,
    local_key: bytes,
    session_key: bytes | None = None,
) -> bytes:
    """Encode a status query (request device DPS snapshot).

    Args:
        sequence: Frame sequence number.
        version: Protocol version string.
        local_key: Device local key.
        session_key: ECDH session key (v3.4/3.5).

    Returns:
        Wire-format status query frame.
    """
    cmd = CMD_DP_QUERY
    return encode_frame(
        cmd,
        b"",
        sequence=sequence,
        version=version,
        local_key=local_key,
        session_key=session_key,
    )


# ── Decoding ──────────────────────────────────────────────────────────────────


def decode_frame(
    data: bytes,
    *,
    version: str,
    local_key: bytes,
    session_key: bytes | None = None,
) -> TuyaFrame:
    """Decode a raw Tuya LAN frame.

    Validates prefix, suffix, length, and CRC before decrypting.

    Args:
        data: Raw bytes received from TCP socket.
        version: Protocol version to use for decryption.
        local_key: 16-byte device local key.
        session_key: ECDH session key (required for v3.4/3.5).

    Returns:
        Decoded :class:`TuyaFrame`.

    Raises:
        MalformedPacketError: If structural validation fails.
        UnsupportedVersionError: If version is not supported.
        CryptoError / AuthenticationError: On decryption failure.
    """
    _check_version(version)

    if len(data) < _MIN_FRAME_SIZE:
        raise MalformedPacketError(
            f"Frame too short: {len(data)} bytes (minimum {_MIN_FRAME_SIZE})"
        )

    prefix, sequence, command, length = _STRUCT_HEADER.unpack_from(data, 0)

    if prefix != FRAME_PREFIX:
        raise MalformedPacketError(
            f"Bad frame prefix: {prefix.hex()} (expected {FRAME_PREFIX.hex()})"
        )

    # The length field encodes: payload_size + CRC(4) + suffix(4) = minimum 8.
    # A value < 8 would make suffix_offset overlap with or precede the header.
    if length < 8:
        raise MalformedPacketError(
            f"Frame length field {length} too small (minimum 8 for CRC + suffix)"
        )
    suffix_offset = _STRUCT_HEADER.size + length - 4  # suffix is last 4 bytes of length
    if suffix_offset + 4 > len(data):
        raise MalformedPacketError(
            f"Frame length field ({length}) exceeds available data ({len(data)} bytes)"
        )

    suffix = data[suffix_offset : suffix_offset + 4]
    if suffix != FRAME_SUFFIX:
        raise MalformedPacketError(
            f"Bad frame suffix: {suffix.hex()} (expected {FRAME_SUFFIX.hex()})"
        )

    # Payload sits between header and CRC (4 bytes before suffix)
    payload_start = _STRUCT_HEADER.size
    payload_end = suffix_offset - 4  # exclude CRC
    if payload_end < payload_start:
        raise MalformedPacketError("Frame has no space for payload between header and CRC")

    raw_payload = data[payload_start:payload_end]
    crc_bytes = data[payload_end : payload_end + 4]
    crc_received = struct.unpack(">I", crc_bytes)[0]

    if len(raw_payload) > MAX_PAYLOAD_SIZE:
        raise MalformedPacketError(
            f"Payload too large: {len(raw_payload)} bytes (max {MAX_PAYLOAD_SIZE})"
        )

    # Verify CRC over header + payload (v3.1-3.3 only; v3.4/3.5 use GCM)
    if version not in VERSIONS_GCM:
        verify_crc32(data[:payload_end], crc_received)

    # Decrypt payload
    if not raw_payload or version == PROTOCOL_31:
        decrypted = raw_payload
    else:
        decrypted = decrypt_payload(version, local_key, raw_payload, session_key)

    return TuyaFrame(
        sequence=sequence,
        command=command,
        version=version,
        payload=decrypted,
        raw=data,
    )


def split_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split a TCP receive buffer into complete frames and a leftover tail.

    Tuya TCP streams may deliver multiple frames in one read, or a frame may
    be split across reads. This function splits on the known prefix/suffix
    boundaries without interpreting content.

    Args:
        buffer: Raw bytes from TCP recv.

    Returns:
        Tuple of (list of complete raw frames, remaining incomplete bytes).
    """
    frames: list[bytes] = []
    offset = 0
    # R40-F4: Count only non-productive iterations (skipped junk bytes), not
    # successful frame extractions.  Counting all iterations incorrectly capped
    # large bursts of valid frames at 4096 total, causing missed DPS updates.
    # A pathological adversarial buffer (e.g. 64 KB of prefix bytes) still
    # triggers at most 4096 skip-passes before we bail out.
    _MAX_SKIP_ITERATIONS = 4096
    _skip_iterations = 0

    while offset < len(buffer):
        # Find next prefix
        idx = buffer.find(FRAME_PREFIX, offset)
        if idx == -1:
            break  # No more frames

        if len(buffer) - idx < _MIN_FRAME_SIZE:
            offset = idx
            break  # Incomplete frame — wait for more data

        # Read the length field
        _, _, _, length = _STRUCT_HEADER.unpack_from(buffer, idx)

        # Reject implausibly large frames before allocating — prevents a
        # crafted device from causing memory amplification via a huge length
        # field.  Legitimate frames are bounded by MAX_PAYLOAD_SIZE + overhead.
        if length > MAX_PAYLOAD_SIZE + 8:  # +8 for CRC(4) + suffix(4)
            _skip_iterations += 1
            if _skip_iterations > _MAX_SKIP_ITERATIONS:
                break  # Prevent O(N) loop on adversarial prefix-heavy buffers
            offset = idx + 1  # skip this prefix byte and search for next frame
            continue

        frame_end = idx + _STRUCT_HEADER.size + length

        if frame_end > len(buffer):
            offset = idx
            break  # Frame is incomplete

        # Verify suffix at the expected position
        expected_suffix_offset = frame_end - 4
        if buffer[expected_suffix_offset:frame_end] != FRAME_SUFFIX:
            # Misaligned — skip this prefix byte and search again
            _skip_iterations += 1
            if _skip_iterations > _MAX_SKIP_ITERATIONS:
                break  # Prevent O(N) loop on adversarial prefix-heavy buffers
            offset = idx + 1
            continue

        # Successful frame extraction — reset skip counter and advance
        _skip_iterations = 0
        frames.append(buffer[idx:frame_end])
        offset = frame_end

    return frames, buffer[offset:]


# ── Session key negotiation frames (v3.4/3.5) ─────────────────────────────────


def encode_session_key_start(
    public_key_bytes: bytes,
    *,
    sequence: int,
    local_key: bytes,
) -> bytes:
    """Encode the SESS_KEY_NEG_START frame for v3.4/3.5 key exchange.

    The START frame carries our X25519 public key, encrypted with the
    device local key (AES-ECB) since the session key is not yet established.

    Args:
        public_key_bytes: 32-byte X25519 public key from our ephemeral key pair.
        sequence: Frame sequence number.
        local_key: 16-byte device local key.

    Returns:
        Wire-format START frame bytes.

    Raises:
        CryptoError: If the local key length is wrong.
    """
    from tuya_cloudless.crypto import derive_ecb_key, encrypt_ecb

    ecb_key = derive_ecb_key(local_key)
    encrypted = encrypt_ecb(ecb_key, public_key_bytes)
    length = len(encrypted) + 8  # +4 CRC +4 suffix
    header = _STRUCT_HEADER.pack(
        FRAME_PREFIX, sequence & 0xFFFFFFFF, CMD_SESS_KEY_NEG_START, length
    )
    body = header + encrypted
    crc = compute_crc32(body)
    return body + struct.pack(">I", crc) + FRAME_SUFFIX


def encode_session_key_finish(
    confirmation_bytes: bytes,
    *,
    sequence: int,
    local_key: bytes,
    session_key: bytes,
) -> bytes:
    """Encode the SESS_KEY_NEG_FINISH frame for v3.4/3.5 key exchange.

    The FINISH frame carries the HMAC confirmation payload, encrypted with
    the newly derived session key (AES-GCM).

    Args:
        confirmation_bytes: HMAC-SHA256 confirmation (typically 32 bytes).
        sequence: Frame sequence number.
        local_key: 16-byte device local key (unused here, kept for symmetry).
        session_key: 16-byte AES session key from ECDH derivation.

    Returns:
        Wire-format FINISH frame bytes.

    Raises:
        CryptoError: If key lengths are wrong.
    """
    from tuya_cloudless.crypto import encrypt_gcm

    encrypted = encrypt_gcm(session_key, confirmation_bytes)
    length = len(encrypted) + 8
    # R40-F9: Mask sequence to 32 bits (consistent with encode_frame — prevents
    # struct.error when callers pass values larger than 0xFFFFFFFF).
    header = _STRUCT_HEADER.pack(
        FRAME_PREFIX, sequence & 0xFFFFFFFF, CMD_SESS_KEY_NEG_FINISH, length
    )
    body = header + encrypted
    # v3.4/3.5 uses sequence counter in place of CRC for GCM frames
    seq_bytes = struct.pack(">I", sequence & 0xFFFFFFFF)
    return body + seq_bytes + FRAME_SUFFIX


def encode_status_response(
    dps: dict[str, Any],
    *,
    sequence: int,
    version: str,
    local_key: bytes,
    session_key: bytes | None = None,
) -> bytes:
    """Encode a DPS status response (device → controller direction).

    Used by the fake Tuya device in integration tests to push DPS updates
    to a connected coordinator.

    Args:
        dps: DPS dict to include in the response payload.
        sequence: Frame sequence number.
        version: Protocol version string.
        local_key: 16-byte device local key.
        session_key: ECDH session key (required for v3.4/3.5).

    Returns:
        Wire-format status response frame bytes.
    """
    payload = json.dumps({"dps": dps}, separators=(",", ":")).encode()
    return encode_frame(
        CMD_STATUS,
        payload,
        sequence=sequence,
        version=version,
        local_key=local_key,
        session_key=session_key,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


def _check_version(version: str) -> None:
    """Raise :class:`UnsupportedVersionError` if version is not supported."""
    from tuya_cloudless.const import SUPPORTED_VERSIONS

    if version not in SUPPORTED_VERSIONS:
        raise UnsupportedVersionError(
            f"Protocol version '{version}' is not supported "
            f"(supported: {sorted(SUPPORTED_VERSIONS)})"
        )
