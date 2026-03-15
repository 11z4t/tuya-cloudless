"""Unit tests for tuya_cloudless.protocol — frame encode/decode."""

from __future__ import annotations

import json
import struct

import pytest
from tuya_cloudless.const import (
    CMD_CONTROL,
    CMD_HEARTBEAT,
    CMD_STATUS,
    FRAME_PREFIX,
    FRAME_SUFFIX,
)
from tuya_cloudless.exceptions import CryptoError, MalformedPacketError, UnsupportedVersionError
from tuya_cloudless.protocol import (
    TuyaFrame,
    decode_frame,
    encode_control,
    encode_frame,
    encode_heartbeat,
    split_frames,
)

_LOCAL_KEY = b"0123456789abcdef"
_VERSION = "3.3"


# ── Encode ────────────────────────────────────────────────────────────────────


class TestEncodeFrame:
    def test_starts_with_prefix(self) -> None:
        raw = encode_frame(
            CMD_HEARTBEAT, b"", sequence=1, version=_VERSION, local_key=_LOCAL_KEY
        )
        assert raw[:4] == FRAME_PREFIX

    def test_ends_with_suffix(self) -> None:
        raw = encode_frame(
            CMD_HEARTBEAT, b"", sequence=1, version=_VERSION, local_key=_LOCAL_KEY
        )
        assert raw[-4:] == FRAME_SUFFIX

    def test_sequence_in_header(self) -> None:
        raw = encode_frame(
            CMD_HEARTBEAT, b"", sequence=42, version=_VERSION, local_key=_LOCAL_KEY
        )
        _, seq, _, _ = struct.unpack_from(">4sIII", raw, 0)
        assert seq == 42

    def test_unsupported_version_raises(self) -> None:
        with pytest.raises(UnsupportedVersionError):
            encode_frame(CMD_HEARTBEAT, b"", sequence=1, version="2.9", local_key=_LOCAL_KEY)

    def test_heartbeat_helper(self) -> None:
        raw = encode_heartbeat(sequence=5, version=_VERSION, local_key=_LOCAL_KEY)
        assert raw[:4] == FRAME_PREFIX


class TestEncodeControl:
    def test_returns_bytes(self) -> None:
        raw = encode_control(
            {"1": True}, sequence=1, version=_VERSION, local_key=_LOCAL_KEY
        )
        assert isinstance(raw, bytes)
        assert len(raw) > 20

    def test_roundtrip_v31(self) -> None:
        """v3.1 is plaintext — we can verify the JSON content."""
        dps = {"1": True, "2": 500}
        raw = encode_frame(
            CMD_CONTROL,
            json.dumps({"dps": dps}, separators=(",", ":")).encode(),
            sequence=1,
            version="3.1",
            local_key=_LOCAL_KEY,
        )
        frame = decode_frame(raw, version="3.1", local_key=_LOCAL_KEY)
        assert frame.dps == {"dps": dps}


# ── Decode ────────────────────────────────────────────────────────────────────


class TestDecodeFrame:
    def _make_v31_frame(self, cmd: int, payload: bytes, seq: int = 1) -> bytes:
        """Helper: build a valid v3.1 frame with correct CRC."""
        from tuya_cloudless.crypto import compute_crc32

        length = len(payload) + 8  # payload + CRC(4) + suffix(4)
        header = struct.pack(">4sIII", FRAME_PREFIX, seq, cmd, length)
        body = header + payload
        crc = compute_crc32(body)
        return body + struct.pack(">I", crc) + FRAME_SUFFIX

    def test_decode_heartbeat(self) -> None:
        raw = self._make_v31_frame(CMD_HEARTBEAT, b"")
        frame = decode_frame(raw, version="3.1", local_key=_LOCAL_KEY)
        assert frame.command == CMD_HEARTBEAT
        assert frame.sequence == 1
        assert frame.payload == b""

    def test_bad_prefix_raises(self) -> None:
        raw = b"\xDE\xAD\xBE\xEF" + b"\x00" * 20
        with pytest.raises(MalformedPacketError):
            decode_frame(raw, version="3.1", local_key=_LOCAL_KEY)

    def test_too_short_raises(self) -> None:
        with pytest.raises(MalformedPacketError):
            decode_frame(b"\x00" * 5, version="3.1", local_key=_LOCAL_KEY)

    def test_bad_crc_raises(self) -> None:
        raw = bytearray(self._make_v31_frame(CMD_STATUS, b"hello"))
        # Flip the CRC bytes
        raw[-8] ^= 0xFF
        with pytest.raises(CryptoError):
            decode_frame(bytes(raw), version="3.1", local_key=_LOCAL_KEY)

    def test_unsupported_version_raises(self) -> None:
        raw = self._make_v31_frame(CMD_HEARTBEAT, b"")
        with pytest.raises(UnsupportedVersionError):
            decode_frame(raw, version="9.9", local_key=_LOCAL_KEY)

    def test_v33_roundtrip(self) -> None:
        payload = b'{"dps":{"1":true}}'
        raw = encode_frame(
            CMD_CONTROL, payload, sequence=7, version="3.3", local_key=_LOCAL_KEY
        )
        frame = decode_frame(raw, version="3.3", local_key=_LOCAL_KEY)
        assert frame.sequence == 7
        assert frame.dps == {"dps": {"1": True}}

    def test_v34_roundtrip(self) -> None:
        session_key = b"\xAB" * 16
        payload = b'{"dps":{"1":false}}'
        raw = encode_frame(
            CMD_CONTROL,
            payload,
            sequence=3,
            version="3.4",
            local_key=_LOCAL_KEY,
            session_key=session_key,
        )
        frame = decode_frame(raw, version="3.4", local_key=_LOCAL_KEY, session_key=session_key)
        assert frame.sequence == 3
        assert frame.dps == {"dps": {"1": False}}


# ── TuyaFrame.dps ─────────────────────────────────────────────────────────────


class TestTuyaFrameDps:
    def test_empty_payload_returns_empty_dict(self) -> None:
        frame = TuyaFrame(sequence=1, command=CMD_HEARTBEAT, version="3.3", payload=b"")
        assert frame.dps == {}

    def test_valid_json(self) -> None:
        frame = TuyaFrame(
            sequence=1,
            command=CMD_CONTROL,
            version="3.3",
            payload=b'{"dps":{"1":true}}',
        )
        assert frame.dps == {"dps": {"1": True}}

    def test_invalid_json_raises(self) -> None:
        from tuya_cloudless.exceptions import ProtocolError

        frame = TuyaFrame(
            sequence=1,
            command=CMD_CONTROL,
            version="3.3",
            payload=b"not json",
        )
        with pytest.raises(ProtocolError):
            _ = frame.dps


# ── split_frames ──────────────────────────────────────────────────────────────


class TestSplitFrames:
    def _simple_frame(self) -> bytes:
        from tuya_cloudless.crypto import compute_crc32

        payload = b""
        length = len(payload) + 8
        header = struct.pack(">4sIII", FRAME_PREFIX, 1, CMD_HEARTBEAT, length)
        body = header + payload
        crc = compute_crc32(body)
        return body + struct.pack(">I", crc) + FRAME_SUFFIX

    def test_single_frame(self) -> None:
        frame = self._simple_frame()
        frames, leftover = split_frames(frame)
        assert len(frames) == 1
        assert leftover == b""

    def test_two_frames(self) -> None:
        frame = self._simple_frame()
        frames, leftover = split_frames(frame + frame)
        assert len(frames) == 2
        assert leftover == b""

    def test_incomplete_frame(self) -> None:
        frame = self._simple_frame()
        partial = frame[:10]
        frames, leftover = split_frames(partial)
        assert frames == []
        assert leftover == partial

    def test_frame_with_leading_garbage(self) -> None:
        frame = self._simple_frame()
        # Prepend some garbage (no valid prefix)
        data = b"\xDE\xAD" + frame
        frames, _leftover = split_frames(data)
        # Should find the valid frame after skipping garbage
        assert len(frames) == 1
