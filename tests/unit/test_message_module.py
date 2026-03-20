"""Comprehensive tests for tuya_cloudless.message — wire-format encode/decode."""

from __future__ import annotations

import struct

import pytest
from tuya_cloudless.crypto import ProtocolVersion, crc32_bytes, hmac_sha256
from tuya_cloudless.exceptions import InvalidMessageError, ProtocolError
from tuya_cloudless.message import (
    HEADER_SIZE,
    PREFIX,
    SUFFIX,
    CommandType,
    MessageBuffer,
    TuyaMessage,
    build_control,
    build_dp_query,
    build_heartbeat,
    build_status_request,
    decode_message,
    encode_message,
    hmac_compare,
)

_LOCAL_KEY = b"0123456789abcdef"


# ── CommandType ─────────────────────────────────────────────────────────────


class TestCommandType:
    def test_known_commands(self) -> None:
        assert CommandType.HEART_BEAT == 0x09
        assert CommandType.CONTROL == 0x07
        assert CommandType.STATUS == 0x08
        assert CommandType.DP_QUERY == 0x0A

    def test_from_int_valid(self) -> None:
        assert CommandType.from_int(0x09) == CommandType.HEART_BEAT

    def test_from_int_unknown_returns_raw_int(self) -> None:
        """Unknown command codes are returned as raw int, not raised.

        This prevents devices with new/extended command codes from being
        counted as decode errors and triggering a permanent reconnect loop.
        """
        result = CommandType.from_int(0xFF)
        assert result == 0xFF
        assert not isinstance(result, CommandType)

    def test_udp_command(self) -> None:
        assert CommandType.UDP == 0x01

    def test_sess_key_neg(self) -> None:
        assert CommandType.SESS_KEY_NEG == 0x04

    def test_update_dps(self) -> None:
        assert CommandType.UPDATE_DPS == 0x12


# ── TuyaMessage ─────────────────────────────────────────────────────────────


class TestTuyaMessage:
    def test_payload_length(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"hello")
        assert msg.payload_length == 5

    def test_empty_payload_length(self) -> None:
        msg = TuyaMessage(sequence=0, command=CommandType.HEART_BEAT, payload=b"")
        assert msg.payload_length == 0

    def test_frozen_dataclass(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        with pytest.raises(AttributeError):
            msg.sequence = 2  # type: ignore[misc]


# ── Encode / Decode roundtrip ───────────────────────────────────────────────


class TestEncodeDecodeV33:
    """Test encode/decode with CRC32 (v3.1/3.3)."""

    def test_heartbeat_roundtrip(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = encode_message(msg, ProtocolVersion.V33)
        decoded = decode_message(encoded, ProtocolVersion.V33)
        assert decoded.sequence == 1
        assert decoded.command == CommandType.HEART_BEAT
        assert decoded.payload == b""

    def test_control_roundtrip(self) -> None:
        payload = b'{"dps":{"1":true}}'
        msg = TuyaMessage(sequence=42, command=CommandType.CONTROL, payload=payload)
        encoded = encode_message(msg, ProtocolVersion.V33)
        decoded = decode_message(encoded, ProtocolVersion.V33)
        assert decoded.sequence == 42
        assert decoded.command == CommandType.CONTROL
        assert decoded.payload == payload

    def test_prefix_and_suffix(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = encode_message(msg, ProtocolVersion.V33)
        assert struct.unpack(">I", encoded[:4])[0] == PREFIX
        assert struct.unpack(">I", encoded[-4:])[0] == SUFFIX

    def test_v31_roundtrip(self) -> None:
        payload = b'{"dps":{"1":false}}'
        msg = TuyaMessage(sequence=5, command=CommandType.STATUS, payload=payload)
        encoded = encode_message(msg, ProtocolVersion.V31)
        decoded = decode_message(encoded, ProtocolVersion.V31)
        assert decoded.payload == payload


class TestEncodeDecodeV34:
    """Test encode/decode with HMAC-SHA256 (v3.4/3.5)."""

    def test_heartbeat_roundtrip(self) -> None:
        msg = TuyaMessage(sequence=10, command=CommandType.HEART_BEAT, payload=b"")
        encoded = encode_message(msg, ProtocolVersion.V34, local_key=_LOCAL_KEY)
        decoded = decode_message(encoded, ProtocolVersion.V34, local_key=_LOCAL_KEY)
        assert decoded.sequence == 10
        assert decoded.command == CommandType.HEART_BEAT

    def test_control_roundtrip_v35(self) -> None:
        payload = b'{"dps":{"1":true,"2":50}}'
        msg = TuyaMessage(sequence=99, command=CommandType.CONTROL, payload=payload)
        encoded = encode_message(msg, ProtocolVersion.V35, local_key=_LOCAL_KEY)
        decoded = decode_message(encoded, ProtocolVersion.V35, local_key=_LOCAL_KEY)
        assert decoded.payload == payload

    def test_encode_requires_key(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        with pytest.raises(ProtocolError, match="HMAC key required"):
            encode_message(msg, ProtocolVersion.V34)

    def test_decode_requires_key(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = encode_message(msg, ProtocolVersion.V34, local_key=_LOCAL_KEY)
        with pytest.raises(ProtocolError, match="HMAC key required"):
            decode_message(encoded, ProtocolVersion.V34)


# ── Decode validation ───────────────────────────────────────────────────────


class TestDecodeValidation:
    def test_too_short(self) -> None:
        with pytest.raises(InvalidMessageError, match="too short"):
            decode_message(b"\x00" * 10, ProtocolVersion.V33)

    def test_bad_prefix(self) -> None:
        bad = b"\xff\xff\xff\xff" + b"\x00" * 20
        with pytest.raises(InvalidMessageError, match="Invalid prefix"):
            decode_message(bad, ProtocolVersion.V33)

    def test_bad_suffix(self) -> None:
        # Build a valid-looking message but with wrong suffix
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = bytearray(encode_message(msg, ProtocolVersion.V33))
        # Corrupt the suffix
        encoded[-1] = 0x00
        with pytest.raises(InvalidMessageError, match="Invalid suffix"):
            decode_message(bytes(encoded), ProtocolVersion.V33)

    def test_crc_mismatch(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = bytearray(encode_message(msg, ProtocolVersion.V33))
        # Corrupt a CRC byte (4 bytes before suffix)
        encoded[-5] ^= 0xFF
        with pytest.raises(InvalidMessageError, match="CRC32 mismatch"):
            decode_message(bytes(encoded), ProtocolVersion.V33)

    def test_hmac_mismatch(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = bytearray(encode_message(msg, ProtocolVersion.V34, local_key=_LOCAL_KEY))
        # Corrupt HMAC region
        encoded[HEADER_SIZE] ^= 0xFF
        with pytest.raises(InvalidMessageError, match="HMAC"):
            decode_message(bytes(encoded), ProtocolVersion.V34, local_key=_LOCAL_KEY)

    def test_truncated_message(self) -> None:
        msg = TuyaMessage(sequence=1, command=CommandType.CONTROL, payload=b"x" * 50)
        encoded = encode_message(msg, ProtocolVersion.V33)
        with pytest.raises(InvalidMessageError):
            decode_message(encoded[:20], ProtocolVersion.V33)


# ── hmac_compare ────────────────────────────────────────────────────────────


class TestHmacCompare:
    def test_equal(self) -> None:
        assert hmac_compare(b"abc", b"abc") is True

    def test_not_equal(self) -> None:
        assert hmac_compare(b"abc", b"xyz") is False

    def test_empty(self) -> None:
        assert hmac_compare(b"", b"") is True


# ── MessageBuffer ───────────────────────────────────────────────────────────


class TestMessageBuffer:
    def _make_encoded(self, seq: int = 1, payload: bytes = b"") -> bytes:
        msg = TuyaMessage(sequence=seq, command=CommandType.HEART_BEAT, payload=payload)
        return encode_message(msg, ProtocolVersion.V33)

    def test_single_message(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        encoded = self._make_encoded()
        buf.feed(encoded)
        msgs = buf.messages()
        assert len(msgs) == 1
        assert msgs[0].command == CommandType.HEART_BEAT

    def test_two_messages(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        buf.feed(self._make_encoded(1) + self._make_encoded(2))
        msgs = buf.messages()
        assert len(msgs) == 2
        assert msgs[0].sequence == 1
        assert msgs[1].sequence == 2

    def test_fragmented_message(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        encoded = self._make_encoded()
        mid = len(encoded) // 2
        buf.feed(encoded[:mid])
        assert buf.messages() == []
        buf.feed(encoded[mid:])
        msgs = buf.messages()
        assert len(msgs) == 1

    def test_garbage_before_prefix(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        garbage = b"\xff\xfe\xfd"
        buf.feed(garbage + self._make_encoded())
        msgs = buf.messages()
        assert len(msgs) == 1

    def test_overflow_raises(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        with pytest.raises(ProtocolError, match="overflow"):
            buf.feed(b"\x00" * (MessageBuffer.MAX_BUFFER_SIZE + 1))

    def test_clear(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        buf.feed(b"\x00" * 100)
        buf.clear()
        assert buf.pending_bytes == 0

    def test_pending_bytes(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        assert buf.pending_bytes == 0
        buf.feed(b"\x00" * 10)
        assert buf.pending_bytes == 10

    def test_no_prefix_found(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        buf.feed(b"\xff" * 50)
        msgs = buf.messages()
        assert msgs == []

    def test_invalid_payload_len_discards_prefix(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V33)
        # Build a fake header with invalid payload_len (too small)
        header = struct.pack(">IIII", PREFIX, 1, 0x09, 2)  # payload_len=2 < chk+suffix
        buf.feed(header + b"\x00" * 20)
        msgs = buf.messages()
        assert msgs == []

    def test_v34_buffer(self) -> None:
        buf = MessageBuffer(ProtocolVersion.V34, local_key=_LOCAL_KEY)
        msg = TuyaMessage(sequence=5, command=CommandType.HEART_BEAT, payload=b"")
        encoded = encode_message(msg, ProtocolVersion.V34, local_key=_LOCAL_KEY)
        buf.feed(encoded)
        msgs = buf.messages()
        assert len(msgs) == 1
        assert msgs[0].sequence == 5


# ── Convenience builders ────────────────────────────────────────────────────


class TestBuilders:
    def test_build_heartbeat(self) -> None:
        msg = build_heartbeat(sequence=7)
        assert msg.command == CommandType.HEART_BEAT
        assert msg.sequence == 7
        assert msg.payload == b""

    def test_build_dp_query_all(self) -> None:
        msg = build_dp_query(sequence=3)
        assert msg.command == CommandType.DP_QUERY
        assert b'"dps"' in msg.payload

    def test_build_dp_query_specific(self) -> None:
        msg = build_dp_query(sequence=4, dps_ids=[1, 2, 3])
        assert msg.command == CommandType.DP_QUERY
        import json

        data = json.loads(msg.payload)
        assert "1" in data["dps"]
        assert "2" in data["dps"]
        assert "3" in data["dps"]

    def test_build_control(self) -> None:
        msg = build_control(sequence=10, dps={"1": True, "2": 50})
        assert msg.command == CommandType.CONTROL
        import json

        data = json.loads(msg.payload)
        assert data["dps"]["1"] is True
        assert data["dps"]["2"] == 50

    def test_build_status_request(self) -> None:
        msg = build_status_request(sequence=1)
        assert msg.command == CommandType.STATUS
        assert msg.payload == b""


# ── ProtocolVersion enum ────────────────────────────────────────────────────


class TestProtocolVersionEnum:
    def test_values(self) -> None:
        assert ProtocolVersion.V31 == "3.1"
        assert ProtocolVersion.V33 == "3.3"
        assert ProtocolVersion.V34 == "3.4"
        assert ProtocolVersion.V35 == "3.5"

    def test_is_string(self) -> None:
        assert isinstance(ProtocolVersion.V33, str)


# ── crc32_bytes / hmac_sha256 helpers ───────────────────────────────────────


class TestCryptoHelpers:
    def test_crc32_bytes_returns_4_bytes(self) -> None:
        result = crc32_bytes(b"hello")
        assert len(result) == 4
        assert isinstance(result, bytes)

    def test_crc32_bytes_deterministic(self) -> None:
        assert crc32_bytes(b"test") == crc32_bytes(b"test")

    def test_crc32_bytes_different_input(self) -> None:
        assert crc32_bytes(b"a") != crc32_bytes(b"b")

    def test_hmac_sha256_returns_32_bytes(self) -> None:
        result = hmac_sha256(b"key", b"data")
        assert len(result) == 32
        assert isinstance(result, bytes)

    def test_hmac_sha256_deterministic(self) -> None:
        assert hmac_sha256(b"k", b"d") == hmac_sha256(b"k", b"d")

    def test_hmac_sha256_different_key(self) -> None:
        assert hmac_sha256(b"k1", b"d") != hmac_sha256(b"k2", b"d")


class TestMessageBufferCorruptLengthErrors:
    """R22-3: _try_extract prefix discards for invalid/oversized length must
    increment _error_count so coordinator consecutive-error reconnect fires."""

    def test_negative_effective_payload_size_counted(self) -> None:
        """A payload_len_field yielding payload_size < 0 must increment error count."""
        buf = MessageBuffer(ProtocolVersion.V33)
        # Craft a frame with FRAME_PREFIX but payload_len_field=3 (too small for
        # chk_size+suffix, yielding payload_size < 0 for v3.3 which has chk_size=4)
        # struct ">4sIII": prefix(4) + seq(4) + cmd(4) + len(4)
        bad_frame = struct.pack(">4sIII", b"\x00\x00\x55\xaa", 1, 0, 3)
        buf.feed(bad_frame)
        buf.messages()  # trigger _try_extract
        assert buf.pop_error_count() >= 1

    def test_oversized_payload_counted(self) -> None:
        """A payload_len_field exceeding MAX_PAYLOAD_SIZE must increment error count."""
        buf = MessageBuffer(ProtocolVersion.V33)
        oversized_len = 65537 + 8  # > MAX_PAYLOAD_SIZE + chk_size + suffix
        bad_frame = struct.pack(">4sIII", b"\x00\x00\x55\xaa", 1, 0, oversized_len)
        buf.feed(bad_frame)
        buf.messages()
        assert buf.pop_error_count() >= 1

    def test_valid_frame_not_counted(self) -> None:
        """A valid frame must not increment the error count."""
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = encode_message(msg, ProtocolVersion.V33)
        buf = MessageBuffer(ProtocolVersion.V33)
        buf.feed(encoded)
        buf.messages()
        assert buf.pop_error_count() == 0


class TestDecodeMessageEdgeCases:
    """Cover decode_message guard clauses not reached via MessageBuffer._try_extract."""

    def _make_frame_with_len(self, payload_len_field: int, total_bytes: int = 24) -> bytes:
        """Build a raw frame with custom payload_len_field and padded to total_bytes."""
        # Header: prefix(4) + seq(4) + cmd(4) + payload_len(4) = 16 bytes
        header = struct.pack(">IIII", 0x000055AA, 1, 0, payload_len_field)
        # Pad to total_bytes (must be >= 24 for min_size check to pass)
        data = header + b"\x00" * (total_bytes - len(header))
        return data

    def test_payload_size_negative_raises(self) -> None:
        """payload_size < 0 raises InvalidMessageError (lines 339-340)."""
        # v3.3: chk_size=4, suffix=4. payload_size = payload_len_field - 8.
        # Use payload_len_field=3 → payload_size = -5 < 0.
        # Frame must be >= HEADER_SIZE + CRC32_SIZE + SUFFIX_SIZE = 24 bytes.
        frame = self._make_frame_with_len(payload_len_field=3, total_bytes=24)
        with pytest.raises(InvalidMessageError, match="payload_len_field"):
            decode_message(frame, ProtocolVersion.V33)

    def test_payload_size_too_large_raises(self) -> None:
        """payload_size > MAX_PAYLOAD_SIZE raises InvalidMessageError (lines 342-343)."""
        from tuya_cloudless.const import MAX_PAYLOAD_SIZE

        # payload_size = payload_len_field - chk - suffix = (MAX+1) + 8
        oversized_len = MAX_PAYLOAD_SIZE + 1 + 8
        frame = self._make_frame_with_len(payload_len_field=oversized_len, total_bytes=24)
        with pytest.raises(InvalidMessageError, match="MAX_PAYLOAD_SIZE"):
            decode_message(frame, ProtocolVersion.V33)

    def test_data_truncated_raises(self) -> None:
        """Truncated data (len < expected_total_bytes) raises InvalidMessageError (lines 347-348)."""  # noqa: E501
        # payload_size=10, so expected_total = 16 + 10 + 4 + 4 = 34 bytes.
        # Provide only 28 bytes.
        frame = self._make_frame_with_len(payload_len_field=10 + 8, total_bytes=28)
        with pytest.raises(InvalidMessageError, match="truncated"):
            decode_message(frame, ProtocolVersion.V33)


class TestDpsPropertyEdgeCases:
    """Cover .dps property guard for non-dict JSON payload (line 204)."""

    def test_dps_list_payload_raises(self) -> None:
        """JSON array payload raises ProtocolError (line 204)."""
        msg = TuyaMessage(sequence=1, command=CommandType.STATUS, payload=b"[1, 2, 3]")
        with pytest.raises(ProtocolError, match="not a JSON object"):
            _ = msg.dps

    def test_dps_string_payload_raises(self) -> None:
        """JSON string payload raises ProtocolError (line 204)."""
        msg = TuyaMessage(sequence=1, command=CommandType.STATUS, payload=b'"hello"')
        with pytest.raises(ProtocolError, match="not a JSON object"):
            _ = msg.dps


class TestDetectVersionFromSize:
    """Cover _detect_version_from_size (lines 291-295)."""

    def test_detects_crc32_version(self) -> None:
        """Returns V33 when payload_len matches CRC32 checksum size."""
        from tuya_cloudless.message import CRC32_SIZE, SUFFIX_SIZE, _detect_version_from_size

        raw_size = 20
        payload_len = raw_size + CRC32_SIZE + SUFFIX_SIZE
        assert _detect_version_from_size(payload_len, raw_size) == ProtocolVersion.V33

    def test_detects_hmac_version(self) -> None:
        """Returns V34 when payload_len matches HMAC checksum size."""
        from tuya_cloudless.message import HMAC_SIZE, SUFFIX_SIZE, _detect_version_from_size

        raw_size = 20
        payload_len = raw_size + HMAC_SIZE + SUFFIX_SIZE
        assert _detect_version_from_size(payload_len, raw_size) == ProtocolVersion.V34

    def test_returns_none_when_ambiguous(self) -> None:
        """Returns None when payload_len doesn't match either checksum pattern."""
        from tuya_cloudless.message import _detect_version_from_size

        assert _detect_version_from_size(99, 20) is None


class TestMessagesExceptionHandling:
    """Cover messages() exception handler loop (lines 584-588)."""

    def test_bad_suffix_counted_as_error(self) -> None:
        """A frame with bad suffix triggers InvalidMessageError in decode_message,
        which is caught by messages() and counted (lines 584-588)."""
        # Build a valid-looking frame then corrupt the suffix bytes.
        msg = TuyaMessage(sequence=1, command=CommandType.HEART_BEAT, payload=b"")
        encoded = bytearray(encode_message(msg, ProtocolVersion.V33))
        # Corrupt the last 4 bytes (suffix)
        encoded[-4:] = b"\xde\xad\xbe\xef"
        buf = MessageBuffer(ProtocolVersion.V33)
        buf.feed(bytes(encoded))
        result = buf.messages()
        # The corrupt frame should not be in result
        assert result == []
        # But should have been counted as an error
        assert buf.pop_error_count() >= 1


class TestMessagesNoProgressGuard:
    """Cover messages() no-progress guard (lines 571-580)."""

    def test_no_progress_guard_clears_buffer_and_counts_error(self) -> None:
        """When _try_extract raises without consuming bytes the guard fires (lines 571-580)."""
        from unittest.mock import patch

        from tuya_cloudless.crypto import ProtocolVersion
        from tuya_cloudless.exceptions import InvalidMessageError
        from tuya_cloudless.message import MessageBuffer

        buf = MessageBuffer(ProtocolVersion.V33)
        buf.feed(b"\x00\x00\x55\xaa" + b"\x00" * 20)  # fake data to fill buffer

        # Patch _try_extract to always raise without consuming any bytes
        with patch.object(buf, "_try_extract", side_effect=InvalidMessageError("stuck")):
            result = buf.messages()

        assert result == []
        assert buf.pending_bytes == 0  # buffer cleared by guard
        # Both the exception handler (+1) and the no-progress guard (+1) increment
        assert buf.pop_error_count() >= 1
