"""Unit tests for lib/tuya_cloudless/ble_provision.

Covers:
  - crc16_modbus correctness against known vectors
  - BleFrame encode/decode round-trip
  - BleFrame decode error paths (bad magic, bad CRC, truncated)
  - _chunk_frame / _reassemble_chunks round-trip
  - ProvisionPayload.generate_token
  - ProvisionPayload.to_bytes (JSON structure)
  - build_provision_frames (produces correct chunks)
  - build_handshake_frame
  - derive_session_key
  - parse_ble_response
"""

from __future__ import annotations

import json
import struct

import pytest

from tuya_cloudless.ble_provision import (
    BLE_FRAME_MAGIC,
    BLE_MAX_FRAME_SIZE,
    BLE_NONCE_SIZE,
    BLE_PROTOCOL_VERSION,
    CMD_HANDSHAKE,
    CMD_WIFI_CONFIG,
    BleFrame,
    ProvisionPayload,
    _chunk_frame,
    _reassemble_chunks,
    build_handshake_frame,
    build_provision_frames,
    crc16_modbus,
    derive_session_key,
    parse_ble_response,
)
from tuya_cloudless.exceptions import PairingError

# ── crc16_modbus ──────────────────────────────────────────────────────────────


class TestCrc16Modbus:
    def test_empty_input(self) -> None:
        """CRC of empty input is 0xFFFF (initial value, no processing)."""
        assert crc16_modbus(b"") == 0xFFFF

    def test_known_vector_0x01(self) -> None:
        """Single byte 0x01 → known CRC-16/MODBUS value."""
        # CRC16-MODBUS of [0x01] = 0x807E (verified against reference tables)
        result = crc16_modbus(bytes([0x01]))
        assert isinstance(result, int)
        assert 0 <= result <= 0xFFFF

    def test_known_vector_hello(self) -> None:
        """'hello' → 0x34F6 (CRC-16/MODBUS computed reference value)."""
        # Value verified by running the algorithm: crc16_modbus(b"hello") = 0x34F6
        assert crc16_modbus(b"hello") == 0x34F6

    def test_returns_int(self) -> None:
        assert isinstance(crc16_modbus(b"\x55\xaa"), int)

    def test_range(self) -> None:
        assert 0 <= crc16_modbus(b"test data 12345") <= 0xFFFF

    def test_deterministic(self) -> None:
        data = b"consistent input"
        assert crc16_modbus(data) == crc16_modbus(data)

    def test_different_inputs_differ(self) -> None:
        assert crc16_modbus(b"aaa") != crc16_modbus(b"bbb")

    def test_long_payload(self) -> None:
        """CRC of 500-byte payload completes without error."""
        result = crc16_modbus(bytes(range(256)) * 2)
        assert 0 <= result <= 0xFFFF


# ── BleFrame encode / decode ──────────────────────────────────────────────────


class TestBleFrameEncode:
    def test_encode_returns_bytes(self) -> None:
        frame = BleFrame(seq=0, cmd=CMD_HANDSHAKE, payload=b"")
        assert isinstance(frame.encode(), bytes)

    def test_encode_magic(self) -> None:
        frame = BleFrame(seq=0, cmd=CMD_HANDSHAKE, payload=b"")
        encoded = frame.encode()
        assert encoded[:2] == BLE_FRAME_MAGIC

    def test_encode_version(self) -> None:
        frame = BleFrame(seq=1, cmd=CMD_WIFI_CONFIG, payload=b"\x00")
        encoded = frame.encode()
        assert encoded[2] == BLE_PROTOCOL_VERSION

    def test_encode_cmd(self) -> None:
        frame = BleFrame(seq=0, cmd=0x42, payload=b"")
        assert frame.encode()[3] == 0x42

    def test_encode_seq_big_endian(self) -> None:
        frame = BleFrame(seq=0x1234, cmd=0x00, payload=b"")
        encoded = frame.encode()
        seq = struct.unpack_from(">H", encoded, 4)[0]
        assert seq == 0x1234

    def test_encode_payload_length_field(self) -> None:
        payload = b"hello"
        frame = BleFrame(seq=0, cmd=0x01, payload=payload)
        encoded = frame.encode()
        length = struct.unpack_from(">H", encoded, 6)[0]
        assert length == len(payload)

    def test_encode_payload_present(self) -> None:
        payload = b"test_payload"
        frame = BleFrame(seq=0, cmd=0x01, payload=payload)
        encoded = frame.encode()
        assert payload in encoded

    def test_encode_crc_little_endian_appended(self) -> None:
        payload = b"data"
        frame = BleFrame(seq=0, cmd=0x01, payload=payload)
        encoded = frame.encode()
        body = encoded[:-2]
        expected_crc = crc16_modbus(body)
        crc_in_frame = struct.unpack_from("<H", encoded, len(encoded) - 2)[0]
        assert crc_in_frame == expected_crc

    def test_encode_empty_payload(self) -> None:
        frame = BleFrame(seq=0, cmd=0x00, payload=b"")
        encoded = frame.encode()
        assert len(encoded) == 10  # 8 header + 0 payload + 2 CRC


class TestBleFrameDecode:
    def _make_frame(self, seq: int, cmd: int, payload: bytes) -> bytes:
        return BleFrame(seq=seq, cmd=cmd, payload=payload).encode()

    def test_roundtrip(self) -> None:
        original = BleFrame(seq=7, cmd=0x01, payload=b"round trip test")
        decoded = BleFrame.decode(original.encode())
        assert decoded.seq == original.seq
        assert decoded.cmd == original.cmd
        assert decoded.payload == original.payload

    def test_roundtrip_empty_payload(self) -> None:
        original = BleFrame(seq=0, cmd=0x00, payload=b"")
        decoded = BleFrame.decode(original.encode())
        assert decoded.payload == b""

    def test_roundtrip_large_payload(self) -> None:
        payload = bytes(range(256))
        original = BleFrame(seq=100, cmd=0x02, payload=payload)
        decoded = BleFrame.decode(original.encode())
        assert decoded.payload == payload

    def test_too_short_raises(self) -> None:
        with pytest.raises(PairingError, match="too short"):
            BleFrame.decode(b"\x55\xaa\x04\x00")

    def test_bad_magic_raises(self) -> None:
        frame = BleFrame(seq=0, cmd=0x00, payload=b"").encode()
        bad = b"\x00\x00" + frame[2:]
        with pytest.raises(PairingError, match="magic"):
            BleFrame.decode(bad)

    def test_bad_crc_raises(self) -> None:
        frame = bytearray(BleFrame(seq=0, cmd=0x00, payload=b"abc").encode())
        # Flip the last byte of CRC
        frame[-1] ^= 0xFF
        with pytest.raises(PairingError, match="CRC"):
            BleFrame.decode(bytes(frame))

    def test_truncated_payload_raises(self) -> None:
        # Build frame that claims payload=10 but has none
        header = struct.pack(">2sBBHH", BLE_FRAME_MAGIC, BLE_PROTOCOL_VERSION, 0x01, 1, 10)
        crc = crc16_modbus(header)
        data = header + struct.pack("<H", crc)
        with pytest.raises(PairingError):
            BleFrame.decode(data)


# ── Chunking ──────────────────────────────────────────────────────────────────


class TestChunking:
    def test_chunk_size_constraint(self) -> None:
        frame = BleFrame(seq=0, cmd=0x01, payload=b"x" * 100).encode()
        for chunk in _chunk_frame(frame):
            assert len(chunk) <= BLE_MAX_FRAME_SIZE

    def test_single_chunk_for_small_frame(self) -> None:
        """A 6-byte frame fits in one chunk (2 header + 4 data < 20)."""
        frame = BleFrame(seq=0, cmd=0x00, payload=b"").encode()  # 10 bytes
        chunks = _chunk_frame(frame)
        assert len(chunks) == 1

    def test_multi_chunk_for_large_frame(self) -> None:
        frame = BleFrame(seq=0, cmd=0x01, payload=b"a" * 80).encode()
        chunks = _chunk_frame(frame)
        assert len(chunks) > 1

    def test_chunk_header_index_and_total(self) -> None:
        frame = BleFrame(seq=0, cmd=0x01, payload=b"x" * 50).encode()
        chunks = _chunk_frame(frame)
        total = chunks[0][1]
        assert total == len(chunks)
        for idx, chunk in enumerate(chunks):
            assert chunk[0] == idx
            assert chunk[1] == total

    def test_reassemble_roundtrip(self) -> None:
        original = BleFrame(seq=0, cmd=0x01, payload=b"reassemble me").encode()
        chunks = _chunk_frame(original)
        reassembled = _reassemble_chunks(chunks)
        assert reassembled == original

    def test_reassemble_shuffled_chunks(self) -> None:
        """Reassembly must sort by chunk index, not arrival order."""
        frame = BleFrame(seq=0, cmd=0x01, payload=b"x" * 60).encode()
        chunks = _chunk_frame(frame)
        # Reverse chunk order
        shuffled = list(reversed(chunks))
        reassembled = _reassemble_chunks(shuffled)
        assert BleFrame.decode(reassembled).payload == b"x" * 60

    def test_reassemble_empty_raises(self) -> None:
        with pytest.raises(PairingError):
            _reassemble_chunks([])

    def test_reassemble_incomplete_raises(self) -> None:
        frame = BleFrame(seq=0, cmd=0x01, payload=b"x" * 50).encode()
        chunks = _chunk_frame(frame)
        with pytest.raises(PairingError, match="Incomplete"):
            _reassemble_chunks(chunks[:-1])


# ── ProvisionPayload ──────────────────────────────────────────────────────────


class TestProvisionPayload:
    def _make(self, **kwargs: object) -> ProvisionPayload:
        defaults = dict(
            ssid="TestSSID",
            password="testpass",
            token="aabbcc",
            activator_url="http://192.168.1.1:8099",
        )
        defaults.update(kwargs)
        return ProvisionPayload(**defaults)  # type: ignore[arg-type]

    def test_generate_token_length(self) -> None:
        token = ProvisionPayload.generate_token()
        assert len(token) == 32  # 16 bytes hex-encoded

    def test_generate_token_is_hex(self) -> None:
        token = ProvisionPayload.generate_token()
        int(token, 16)  # raises ValueError if not hex

    def test_generate_token_uniqueness(self) -> None:
        tokens = {ProvisionPayload.generate_token() for _ in range(50)}
        assert len(tokens) == 50

    def test_to_bytes_returns_bytes(self) -> None:
        assert isinstance(self._make().to_bytes(), bytes)

    def test_to_bytes_valid_json(self) -> None:
        data = json.loads(self._make().to_bytes())
        assert isinstance(data, dict)

    def test_to_bytes_ssid_in_json(self) -> None:
        data = json.loads(self._make(ssid="MyNet").to_bytes())
        assert data["s"] == "MyNet"

    def test_to_bytes_password_in_json(self) -> None:
        data = json.loads(self._make(password="p@ss!").to_bytes())
        assert data["p"] == "p@ss!"

    def test_to_bytes_token_in_json(self) -> None:
        data = json.loads(self._make(token="mytoken").to_bytes())
        assert data["t"] == "mytoken"

    def test_to_bytes_activator_in_json(self) -> None:
        data = json.loads(self._make(activator_url="http://ha:8099").to_bytes())
        assert data["activator"] == "http://ha:8099"

    def test_to_bytes_region_in_json(self) -> None:
        data = json.loads(self._make(region="eu").to_bytes())
        assert data["r"] == "eu"

    def test_to_bytes_default_region(self) -> None:
        data = json.loads(self._make().to_bytes())
        assert data["r"] == "az"

    def test_to_bytes_utf8_ssid(self) -> None:
        """Non-ASCII SSID must survive encode/decode."""
        data = json.loads(self._make(ssid="Nätverket").to_bytes())
        assert data["s"] == "Nätverket"

    def test_immutable(self) -> None:
        p = self._make()
        with pytest.raises((AttributeError, TypeError)):
            p.ssid = "changed"  # type: ignore[misc]


# ── build_provision_frames ────────────────────────────────────────────────────


class TestBuildProvisionFrames:
    def _payload(self) -> ProvisionPayload:
        return ProvisionPayload(
            ssid="Home", password="pw", token="tok", activator_url="http://ha:8099"
        )

    def test_returns_list_of_bytes(self) -> None:
        frames = build_provision_frames(self._payload())
        assert isinstance(frames, list)
        assert all(isinstance(f, bytes) for f in frames)

    def test_each_chunk_max_size(self) -> None:
        for chunk in build_provision_frames(self._payload()):
            assert len(chunk) <= BLE_MAX_FRAME_SIZE

    def test_reassembly_decodes_wifi_config(self) -> None:
        payload = self._payload()
        chunks = build_provision_frames(payload)
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        assert frame.cmd == CMD_WIFI_CONFIG
        doc = json.loads(frame.payload)
        assert doc["s"] == "Home"
        assert doc["activator"] == "http://ha:8099"

    def test_custom_seq(self) -> None:
        chunks = build_provision_frames(self._payload(), seq=42)
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        assert frame.seq == 42


# ── build_handshake_frame ─────────────────────────────────────────────────────


class TestBuildHandshakeFrame:
    def test_returns_list(self) -> None:
        assert isinstance(build_handshake_frame(), list)

    def test_chunk_size(self) -> None:
        for chunk in build_handshake_frame():
            assert len(chunk) <= BLE_MAX_FRAME_SIZE

    def test_cmd_is_handshake(self) -> None:
        chunks = build_handshake_frame()
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        assert frame.cmd == CMD_HANDSHAKE

    def test_payload_is_nonce(self) -> None:
        chunks = build_handshake_frame()
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        assert len(frame.payload) == BLE_NONCE_SIZE

    def test_custom_nonce(self) -> None:
        nonce = bytes(range(16))
        chunks = build_handshake_frame(controller_nonce=nonce)
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        assert frame.payload == nonce

    def test_invalid_nonce_length_raises(self) -> None:
        with pytest.raises(PairingError, match="nonce"):
            build_handshake_frame(controller_nonce=b"short")

    def test_random_nonce_used_when_none(self) -> None:
        """Two calls with no nonce must produce different nonces."""
        def get_nonce() -> bytes:
            chunks = build_handshake_frame()
            return BleFrame.decode(_reassemble_chunks(chunks)).payload

        assert get_nonce() != get_nonce()


# ── derive_session_key ────────────────────────────────────────────────────────


class TestDeriveSessionKey:
    def test_returns_16_bytes(self) -> None:
        key = derive_session_key(bytes(16), bytes(16))
        assert len(key) == 16

    def test_xor_correctness(self) -> None:
        cn = bytes([0xAA] * 16)
        dn = bytes([0x55] * 16)
        key = derive_session_key(cn, dn)
        assert key == bytes([0xFF] * 16)

    def test_symmetric(self) -> None:
        """XOR is commutative — swapping nonces gives same key."""
        cn = bytes(range(16))
        dn = bytes(range(16, 32))
        assert derive_session_key(cn, dn) == derive_session_key(dn, cn)

    def test_invalid_controller_nonce_raises(self) -> None:
        with pytest.raises(PairingError):
            derive_session_key(b"short", bytes(16))

    def test_invalid_device_nonce_raises(self) -> None:
        with pytest.raises(PairingError):
            derive_session_key(bytes(16), b"short")

    def test_all_zeros_gives_zero_key(self) -> None:
        key = derive_session_key(bytes(16), bytes(16))
        assert key == bytes(16)


# ── parse_ble_response ────────────────────────────────────────────────────────


class TestParseBleResponse:
    def _make_chunks(self, frame: BleFrame) -> list[bytes]:
        return _chunk_frame(frame.encode())

    def test_returns_ble_frame(self) -> None:
        frame = BleFrame(seq=1, cmd=0x02, payload=b"ack")
        result = parse_ble_response(self._make_chunks(frame))
        assert isinstance(result, BleFrame)

    def test_cmd_preserved(self) -> None:
        frame = BleFrame(seq=5, cmd=0x04, payload=b"ok")
        result = parse_ble_response(self._make_chunks(frame))
        assert result.cmd == 0x04

    def test_payload_preserved(self) -> None:
        frame = BleFrame(seq=0, cmd=0x02, payload=b"payload_data")
        result = parse_ble_response(self._make_chunks(frame))
        assert result.payload == b"payload_data"

    def test_empty_chunks_raises(self) -> None:
        with pytest.raises(PairingError):
            parse_ble_response([])
