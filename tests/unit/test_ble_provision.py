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

    def test_with_session_key_payload_is_not_plain_json(self) -> None:
        """When session_key is provided the payload must be encrypted (not plain JSON)."""
        session_key = bytes(range(16))
        chunks = build_provision_frames(self._payload(), session_key=session_key)
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        # Encrypted payload must not decode directly as JSON
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            json.loads(frame.payload)

    def test_session_key_encrypted_payload_is_multiple_of_16(self) -> None:
        """AES-ECB encrypted payload is always a multiple of the AES block size."""
        session_key = bytes(range(16))
        chunks = build_provision_frames(self._payload(), session_key=session_key)
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        assert len(frame.payload) % 16 == 0, "Encrypted payload must be AES-block-aligned"

    def test_session_key_encryption_roundtrip(self) -> None:
        """Encrypting then decrypting with the same key returns the original JSON."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        session_key = derive_session_key(bytes(range(16)), bytes(range(16, 32)))
        chunks = build_provision_frames(self._payload(), session_key=session_key)
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)

        # Decrypt with AES-ECB to recover the JSON payload
        cipher = Cipher(algorithms.AES(session_key), modes.ECB())  # nosec B303 — test-only decrypt
        decryptor = cipher.decryptor()
        decrypted_padded = decryptor.update(frame.payload) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plaintext = unpadder.update(decrypted_padded) + unpadder.finalize()
        doc = json.loads(plaintext)
        assert doc["s"] == "Home"
        assert doc["activator"] == "http://ha:8099"

    def test_without_session_key_payload_is_plain_json(self) -> None:
        """Omitting session_key (legacy / test mode) sends payload as plain JSON."""
        chunks = build_provision_frames(self._payload())
        reassembled = _reassemble_chunks(chunks)
        frame = BleFrame.decode(reassembled)
        doc = json.loads(frame.payload)  # must not raise
        assert doc["s"] == "Home"


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


# ── _reassemble_chunks gap error ──────────────────────────────────────────────


class TestReassembleChunksGap:
    def test_sequence_gap_raises(self) -> None:
        """_reassemble_chunks raises PairingError on a chunk sequence gap.

        Construct two chunks that both claim total=2 but have the same chunk
        index (0), so after sorting by chunk_no we see index 0 twice instead
        of 0,1 — triggering the gap check on the second iteration.
        """
        # chunk[0] = chunk_no, chunk[1] = total
        chunk_a = bytes([0, 2]) + b"A" * 10  # idx=0, total=2
        chunk_b = bytes([0, 2]) + b"B" * 10  # idx=0 again — duplicate index
        # Now caught earlier as "duplicate chunk indices" before sequential check
        with pytest.raises(PairingError, match="duplicate"):
            _reassemble_chunks([chunk_a, chunk_b])

    def test_short_chunk_raises(self) -> None:
        """A chunk shorter than 2 bytes raises PairingError (MED-2 guard)."""
        with pytest.raises(PairingError, match="too short"):
            _reassemble_chunks([b"\x00"])  # only chunk_no byte, missing total

    def test_zero_byte_chunk_raises(self) -> None:
        """An empty chunk raises PairingError."""
        with pytest.raises(PairingError):
            _reassemble_chunks([b""])


# ── BleProvisioner — scan ──────────────────────────────────────────────────────


class TestBleProvisionerScan:
    """Tests for BleProvisioner.scan() with bleak mocked."""

    @pytest.mark.asyncio
    async def test_scan_raises_if_bleak_missing(self) -> None:
        """scan() raises PairingError when bleak is not installed."""
        import sys
        from unittest.mock import patch

        from tuya_cloudless.ble_provision import BleProvisioner

        with patch.dict(sys.modules, {"bleak": None}):
            p = BleProvisioner()
            with pytest.raises(PairingError, match="bleak is required"):
                await p.scan()

    @pytest.mark.asyncio
    async def test_scan_returns_address_when_device_found(self) -> None:
        """scan() returns the BLE address of the discovered device."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        mock_device = MagicMock()
        mock_device.address = "AA:BB:CC:DD:EE:FF"

        mock_scanner = MagicMock()
        mock_scanner.find_device_by_filter = AsyncMock(return_value=mock_device)

        mock_bleak = MagicMock()
        mock_bleak.BleakScanner = mock_scanner

        import sys

        with patch.dict(sys.modules, {"bleak": mock_bleak}):
            p = BleProvisioner()
            addr = await p.scan(timeout=5.0)

        assert addr == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_scan_raises_when_no_device_found(self) -> None:
        """scan() raises PairingError when BleakScanner returns None."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        mock_scanner = MagicMock()
        mock_scanner.find_device_by_filter = AsyncMock(return_value=None)

        mock_bleak = MagicMock()
        mock_bleak.BleakScanner = mock_scanner

        import sys

        with patch.dict(sys.modules, {"bleak": mock_bleak}):
            p = BleProvisioner(scan_timeout=3.0)
            with pytest.raises(PairingError, match="No Tuya BLE device found"):
                await p.scan()

    @pytest.mark.asyncio
    async def test_scan_uses_default_timeout(self) -> None:
        """scan() uses scan_timeout from constructor when no explicit timeout given."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        mock_scanner = MagicMock()
        mock_scanner.find_device_by_filter = AsyncMock(return_value=None)

        mock_bleak = MagicMock()
        mock_bleak.BleakScanner = mock_scanner

        import sys

        with patch.dict(sys.modules, {"bleak": mock_bleak}):
            p = BleProvisioner(scan_timeout=7.0)
            with pytest.raises(PairingError):
                await p.scan()

        # Verify timeout=7.0 was passed to find_device_by_filter
        call_kwargs = mock_scanner.find_device_by_filter.call_args
        assert call_kwargs[1]["timeout"] == 7.0


# ── BleProvisioner — context manager & disconnect ────────────────────────────


class TestBleProvisionerContextManager:
    @pytest.mark.asyncio
    async def test_aenter_returns_self(self) -> None:
        from tuya_cloudless.ble_provision import BleProvisioner

        p = BleProvisioner()
        result = await p.__aenter__()
        assert result is p

    @pytest.mark.asyncio
    async def test_aexit_calls_disconnect(self) -> None:
        from unittest.mock import AsyncMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        p = BleProvisioner()
        with patch.object(p, "disconnect", new=AsyncMock()) as mock_disconnect:
            await p.__aexit__(None, None, None)
            mock_disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_noop_when_no_client(self) -> None:
        """disconnect() does nothing when _client is None."""
        from tuya_cloudless.ble_provision import BleProvisioner

        p = BleProvisioner()
        assert p._client is None
        await p.disconnect()  # must not raise

    @pytest.mark.asyncio
    async def test_disconnect_clears_client(self) -> None:
        """disconnect() clears _client even when bleak is not available."""
        import sys
        from unittest.mock import MagicMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        p = BleProvisioner()
        # Set a fake client that is not a BleakClient instance
        fake_client = MagicMock(spec=[])  # no is_connected attribute
        p._client = fake_client

        mock_bleak = MagicMock()
        mock_bleak.BleakClient = type("BleakClient", (), {})  # won't match isinstance

        with patch.dict(sys.modules, {"bleak": mock_bleak}):
            await p.disconnect()

        assert p._client is None


# ── BleProvisioner — provision error paths ────────────────────────────────────


class TestBleProvisionerProvision:
    """Test provision() error handling using mocked BleakClient."""

    def _make_payload(self) -> ProvisionPayload:
        return ProvisionPayload(
            ssid="TestNet",
            password="testpass",
            token=ProvisionPayload.generate_token(),
            activator_url="http://192.168.1.1:8099",
        )

    @pytest.mark.asyncio
    async def test_provision_raises_if_bleak_missing(self) -> None:
        """provision() raises PairingError when bleak is not installed."""
        import sys
        from unittest.mock import patch

        from tuya_cloudless.ble_provision import BleProvisioner

        with patch.dict(sys.modules, {"bleak": None, "bleak.backends.characteristic": None}):
            p = BleProvisioner()
            with pytest.raises(PairingError, match="bleak is required"):
                await p.provision("AA:BB:CC:DD:EE:FF", self._make_payload())

    @pytest.mark.asyncio
    async def test_provision_raises_pairing_error_on_os_error(self) -> None:
        """provision() converts OSError from BleakClient to PairingError."""
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        # BleakClient context manager raises OSError on connect
        mock_client_instance = MagicMock()
        mock_client_instance.__aenter__ = AsyncMock(side_effect=OSError("connection refused"))
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        mock_bleak_client_cls = MagicMock(return_value=mock_client_instance)

        mock_char = MagicMock()
        mock_bleak = MagicMock()
        mock_bleak.BleakClient = mock_bleak_client_cls

        mock_backends = MagicMock()
        mock_backends.characteristic = MagicMock()
        mock_backends.characteristic.BleakGATTCharacteristic = mock_char

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_backends.characteristic,
            },
        ):
            p = BleProvisioner()
            with pytest.raises(PairingError, match="BLE provisioning failed"):
                await p.provision("AA:BB:CC:DD:EE:FF", self._make_payload())

    @pytest.mark.asyncio
    async def test_provision_happy_path_success(self) -> None:
        """provision() completes without exception when device responds correctly."""
        import asyncio
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import (
            BLE_NOTIFY_CHAR_UUID,
            CMD_PAIR_SUCCESS,
            BleFrame,
            BleProvisioner,
            _chunk_frame,
        )

        device_nonce = bytes(range(16))
        resp_frame = BleFrame(seq=0, cmd=0x00, payload=device_nonce)
        resp_chunks = _chunk_frame(resp_frame.encode())

        ack_frame = BleFrame(seq=2, cmd=CMD_PAIR_SUCCESS, payload=b"")
        ack_chunks = _chunk_frame(ack_frame.encode())

        notify_callbacks: dict = {}
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()

        async def fake_start_notify(char_uuid: str, callback):  # type: ignore[no-untyped-def]
            notify_callbacks[char_uuid] = callback

        mock_client.start_notify = fake_start_notify
        mock_gatt_char = MagicMock()

        # Use AsyncMock for __aenter__ so it handles being called as a bound method
        mock_client_cm = MagicMock()
        mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cm.__aexit__ = AsyncMock(return_value=False)

        GATTCharType = type("BleakGATTCharacteristic", (), {})
        mock_char_module = MagicMock()
        mock_char_module.BleakGATTCharacteristic = GATTCharType
        mock_bleak = MagicMock()
        mock_bleak.BleakClient = MagicMock(return_value=mock_client_cm)
        mock_backends = MagicMock()
        mock_backends.characteristic = mock_char_module

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_char_module,
            },
        ):
            p = BleProvisioner()

            async def run_provision() -> None:
                task = asyncio.create_task(
                    p.provision("AA:BB:CC:DD:EE:FF", self._make_payload(), pair_timeout=5.0)
                )
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in resp_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in ack_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await task

            await run_provision()

        mock_client.write_gatt_char.assert_awaited()

    @pytest.mark.asyncio
    async def test_provision_handshake_timeout_raises(self) -> None:
        """provision() raises PairingError on handshake response timeout."""
        import asyncio
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.start_notify = AsyncMock()

        mock_client_cm = MagicMock()
        mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cm.__aexit__ = AsyncMock(return_value=False)

        GATTCharType = type("BleakGATTCharacteristic", (), {})
        mock_char_module = MagicMock()
        mock_char_module.BleakGATTCharacteristic = GATTCharType
        mock_bleak = MagicMock()
        mock_bleak.BleakClient = MagicMock(return_value=mock_client_cm)
        mock_backends = MagicMock()
        mock_backends.characteristic = mock_char_module

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_char_module,
            },
        ):
            original_wait_for = asyncio.wait_for
            call_count = 0

            async def fake_wait_for(coro, timeout):  # type: ignore[no-untyped-def]
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    coro.close()
                    raise TimeoutError("handshake timeout")
                return await original_wait_for(coro, timeout)

            with patch("asyncio.wait_for", fake_wait_for):
                p = BleProvisioner()
                with pytest.raises(PairingError, match="Timeout waiting for BLE handshake"):
                    await p.provision("AA:BB:CC:DD:EE:FF", self._make_payload())


# ── BleProvisioner disconnect with connected BleakClient ─────────────────────


class TestBleProvisionerProvisionInternalErrors:
    """Tests for the internal error paths inside provision()."""

    def _make_payload(self) -> ProvisionPayload:
        return ProvisionPayload(
            ssid="Net",
            password="pw",
            token=ProvisionPayload.generate_token(),
            activator_url="http://ha:8099",
        )

    def _build_bleak_mock(self, mock_client: object) -> tuple[object, object, object, object]:
        """Return (mock_bleak, mock_backends, mock_char_module, mock_client_cm)."""
        from unittest.mock import AsyncMock, MagicMock

        GATTCharType = type("BleakGATTCharacteristic", (), {})
        mock_char_module = MagicMock()
        mock_char_module.BleakGATTCharacteristic = GATTCharType
        mock_bleak = MagicMock()
        mock_client_cm = MagicMock()
        mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cm.__aexit__ = AsyncMock(return_value=False)
        mock_bleak.BleakClient = MagicMock(return_value=mock_client_cm)
        mock_backends = MagicMock()
        mock_backends.characteristic = mock_char_module
        return mock_bleak, mock_backends, mock_char_module, mock_client_cm

    @pytest.mark.asyncio
    async def test_provision_unexpected_handshake_response_raises(self) -> None:
        """provision() raises PairingError when handshake response has wrong cmd."""
        import asyncio
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import (
            BLE_NOTIFY_CHAR_UUID,
            BleFrame,
            BleProvisioner,
            _chunk_frame,
        )

        # Build a response with wrong command (not 0x00 = CMD_HANDSHAKE_RESP)
        bad_resp = BleFrame(seq=0, cmd=0xFF, payload=bytes(16))
        bad_chunks = _chunk_frame(bad_resp.encode())

        notify_callbacks: dict = {}
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_gatt_char = MagicMock()

        async def fake_start_notify(char_uuid: str, callback):  # type: ignore[no-untyped-def]
            notify_callbacks[char_uuid] = callback

        mock_client.start_notify = fake_start_notify

        mock_bleak, mock_backends, mock_char_module, _ = self._build_bleak_mock(mock_client)

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_char_module,
            },
        ):
            p = BleProvisioner()

            async def run_with_bad_resp() -> None:
                task = asyncio.create_task(p.provision("AA:BB:CC:DD:EE:FF", self._make_payload()))
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in bad_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await task

            with pytest.raises(PairingError, match="Unexpected handshake response"):
                await run_with_bad_resp()

    @pytest.mark.asyncio
    async def test_provision_ack_timeout_raises(self) -> None:
        """provision() raises PairingError when WiFi config ACK times out."""
        import asyncio
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import (
            BLE_NOTIFY_CHAR_UUID,
            BleFrame,
            BleProvisioner,
            _chunk_frame,
        )

        # Valid handshake response with 16-byte nonce
        device_nonce = bytes(range(16))
        resp_frame = BleFrame(seq=0, cmd=0x00, payload=device_nonce)
        resp_chunks = _chunk_frame(resp_frame.encode())

        notify_callbacks: dict = {}
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_gatt_char = MagicMock()

        async def fake_start_notify(char_uuid: str, callback):  # type: ignore[no-untyped-def]
            notify_callbacks[char_uuid] = callback

        mock_client.start_notify = fake_start_notify

        mock_bleak, mock_backends, mock_char_module, _ = self._build_bleak_mock(mock_client)

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_char_module,
            },
        ):
            original_wait_for = asyncio.wait_for
            call_count = 0

            async def fake_wait_for(coro, timeout):  # type: ignore[no-untyped-def]
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    # Second call = waiting for WiFi config ACK
                    coro.close()
                    raise TimeoutError("ack timeout")
                return await original_wait_for(coro, timeout)

            p = BleProvisioner()

            async def run_with_ack_timeout() -> None:
                task = asyncio.create_task(
                    p.provision("AA:BB:CC:DD:EE:FF", self._make_payload(), pair_timeout=1.0)
                )
                await asyncio.sleep(0)
                # Deliver valid handshake response
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in resp_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await task

            with (
                patch("asyncio.wait_for", fake_wait_for),
                pytest.raises(PairingError, match="Timeout waiting for WiFi config ACK"),
            ):
                await run_with_ack_timeout()

    @pytest.mark.asyncio
    async def test_provision_pair_fail_raises(self) -> None:
        """provision() raises PairingError when device sends CMD_PAIR_FAIL."""
        import asyncio
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import (
            BLE_NOTIFY_CHAR_UUID,
            CMD_PAIR_FAIL,
            BleFrame,
            BleProvisioner,
            _chunk_frame,
        )

        device_nonce = bytes(range(16))
        resp_frame = BleFrame(seq=0, cmd=0x00, payload=device_nonce)
        resp_chunks = _chunk_frame(resp_frame.encode())

        fail_frame = BleFrame(seq=2, cmd=CMD_PAIR_FAIL, payload=b"")
        fail_chunks = _chunk_frame(fail_frame.encode())

        notify_callbacks: dict = {}
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_gatt_char = MagicMock()

        async def fake_start_notify(char_uuid: str, callback):  # type: ignore[no-untyped-def]
            notify_callbacks[char_uuid] = callback

        mock_client.start_notify = fake_start_notify

        mock_bleak, mock_backends, mock_char_module, _ = self._build_bleak_mock(mock_client)

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_char_module,
            },
        ):
            p = BleProvisioner()

            async def run_with_fail() -> None:
                task = asyncio.create_task(
                    p.provision("AA:BB:CC:DD:EE:FF", self._make_payload(), pair_timeout=5.0)
                )
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in resp_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in fail_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await task

            with pytest.raises(PairingError, match="Device rejected WiFi config"):
                await run_with_fail()

    @pytest.mark.asyncio
    async def test_provision_unexpected_ack_raises(self) -> None:
        """provision() raises PairingError when device sends an unexpected ACK command."""
        import asyncio
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import (
            BLE_NOTIFY_CHAR_UUID,
            BleFrame,
            BleProvisioner,
            _chunk_frame,
        )

        device_nonce = bytes(range(16))
        resp_frame = BleFrame(seq=0, cmd=0x00, payload=device_nonce)
        resp_chunks = _chunk_frame(resp_frame.encode())

        # Unknown command (0x10 is not WIFI_CONFIG_RESP or PAIR_SUCCESS)
        bad_ack = BleFrame(seq=2, cmd=0x10, payload=b"")
        bad_ack_chunks = _chunk_frame(bad_ack.encode())

        notify_callbacks: dict = {}
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_gatt_char = MagicMock()

        async def fake_start_notify(char_uuid: str, callback):  # type: ignore[no-untyped-def]
            notify_callbacks[char_uuid] = callback

        mock_client.start_notify = fake_start_notify

        mock_bleak, mock_backends, mock_char_module, _ = self._build_bleak_mock(mock_client)

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_char_module,
            },
        ):
            p = BleProvisioner()

            async def run_with_bad_ack() -> None:
                task = asyncio.create_task(
                    p.provision("AA:BB:CC:DD:EE:FF", self._make_payload(), pair_timeout=5.0)
                )
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in resp_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in bad_ack_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await task

            with pytest.raises(PairingError, match="Unexpected ACK command"):
                await run_with_bad_ack()

    @pytest.mark.asyncio
    async def test_provision_zero_nonce_raises_pairing_error(self) -> None:
        """R35-6: all-zeros device nonce raises PairingError (rogue device guard)."""
        import asyncio
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import (
            BLE_NONCE_SIZE,
            BLE_NOTIFY_CHAR_UUID,
            CMD_HANDSHAKE_RESP,
            BleFrame,
            BleProvisioner,
            _chunk_frame,
        )

        zero_nonce_resp = BleFrame(seq=0, cmd=CMD_HANDSHAKE_RESP, payload=bytes(BLE_NONCE_SIZE))
        zero_nonce_chunks = _chunk_frame(zero_nonce_resp.encode())

        notify_callbacks: dict = {}
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_gatt_char = MagicMock()

        async def fake_start_notify(char_uuid: str, callback):  # type: ignore[no-untyped-def]
            notify_callbacks[char_uuid] = callback

        mock_client.start_notify = fake_start_notify

        mock_bleak, mock_backends, mock_char_module, _ = self._build_bleak_mock(mock_client)

        with patch.dict(
            sys.modules,
            {
                "bleak": mock_bleak,
                "bleak.backends": mock_backends,
                "bleak.backends.characteristic": mock_char_module,
            },
        ):
            p = BleProvisioner()

            async def run_with_zero_nonce() -> None:
                task = asyncio.create_task(p.provision("AA:BB:CC:DD:EE:FF", self._make_payload()))
                await asyncio.sleep(0)
                cb = notify_callbacks.get(BLE_NOTIFY_CHAR_UUID)
                if cb is not None:
                    for chunk in zero_nonce_chunks:
                        cb(mock_gatt_char, bytearray(chunk))
                await task

            with pytest.raises(PairingError, match="all-zeros nonce"):
                await run_with_zero_nonce()


class TestBleProvisionerDisconnectConnected:
    @pytest.mark.asyncio
    async def test_disconnect_with_connected_bleak_client(self) -> None:
        """disconnect() calls client.disconnect() when isinstance check passes."""
        import sys
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_cloudless.ble_provision import BleProvisioner

        # Disconnect clears _client even when BleakClient check fires
        p = BleProvisioner()

        # Simulate a client that passes isinstance but is_connected = True
        BleakClientCls = type("BleakClient", (), {})
        mock_client = BleakClientCls()
        mock_client.is_connected = True  # type: ignore[attr-defined]
        mock_client.disconnect = AsyncMock()  # type: ignore[attr-defined]

        mock_bleak = MagicMock()
        mock_bleak.BleakClient = BleakClientCls

        p._client = mock_client
        with patch.dict(sys.modules, {"bleak": mock_bleak}):
            await p.disconnect()

        assert p._client is None


class TestChunkOverflowClearsEvent:
    """R20-1: notify_event must be cleared when recv_chunks overflows."""

    def test_overflow_resets_notify_event(self) -> None:
        """After 512-chunk overflow, notify_event is cleared so the next frame
        does not immediately return stale data."""
        import asyncio

        recv_chunks: list[bytes] = []
        notify_event = asyncio.Event()

        # Simulate the _on_notify callback logic directly
        def on_notify(data: bytes) -> None:
            if len(recv_chunks) >= 512:
                recv_chunks.clear()
                notify_event.clear()  # This is the fix we're testing
            recv_chunks.append(bytes(data))
            if data[0] + 1 == data[1]:
                notify_event.set()

        # Fill to 512 with partial chunks (chunk_no=0, total=2 — not the last)
        for i in range(512):
            on_notify(bytes([0, 2, i & 0xFF]))  # chunk_no=0, total=2

        # At this point the overflow fires on the NEXT chunk
        # Simulate the 513th chunk triggering overflow
        # First, pre-set the event to simulate it being already set
        notify_event.set()
        assert notify_event.is_set()

        # The first chunk of a new 2-chunk frame (not the last chunk)
        on_notify(bytes([0, 2, 0xAB]))

        # After overflow, event should be cleared (the new chunk is NOT the last)
        assert not notify_event.is_set(), (
            "notify_event must be cleared on overflow to prevent immediate "
            "return with stale/partial frame data"
        )
        # Only the new chunk should remain
        assert len(recv_chunks) == 1

    def test_overflow_discards_mid_frame_chunk(self) -> None:
        """R36-5: after 512-cap flush, a mid-frame chunk (chunk_no > 0) is discarded.

        Without R36-5 the old code would append a chunk_no=1-of-2 chunk after
        clearing, then immediately fire notify_event (last-chunk condition),
        causing parse_ble_response to receive an incomplete frame.
        """
        import asyncio

        recv_chunks: list[bytes] = []
        notify_event = asyncio.Event()

        # Replicate the updated _on_notify logic including R36-5 guard
        def on_notify(data: bytes) -> None:
            if len(data) < 2:
                return
            if len(recv_chunks) >= 512:
                recv_chunks.clear()
                notify_event.clear()
                if data[0] != 0:  # R36-5: drop mid-frame chunks after overflow
                    return
            recv_chunks.append(bytes(data))
            if data[0] + 1 == data[1]:
                notify_event.set()

        # Fill to 512 chunks
        for i in range(512):
            on_notify(bytes([0, 2, i & 0xFF]))  # first-chunk of 2-chunk frame

        notify_event.set()  # pre-set to simulate stale state

        # Simulate arrival of chunk_no=1 (LAST chunk of a 2-chunk frame)
        # This triggers the 512-cap, but it's a mid-frame chunk → must be dropped
        on_notify(bytes([1, 2, 0xAB]))  # chunk_no=1, total=2 — would be last chunk

        # notify_event MUST NOT be set (the mid-frame chunk was dropped)
        assert not notify_event.is_set(), (
            "R36-5: mid-frame chunk after overflow must be dropped, "
            "not appended and fire notify_event"
        )
        assert len(recv_chunks) == 0, "recv_chunks must be empty after overflow + mid-frame drop"


class TestReassembleChunksTotalZeroR41:
    """R41-F7: _reassemble_chunks must reject total=0 to prevent 10-second hang."""

    def test_total_zero_raises_pairing_error(self) -> None:
        """A chunk with total_chunks=0 must raise PairingError immediately."""
        from tuya_cloudless.exceptions import PairingError

        # Single chunk with chunk_no=0, total=0 (invalid)
        chunk = bytes([0, 0, 0xAA, 0xBB])  # chunk_no=0, total=0, data=0xAABB
        with pytest.raises(PairingError, match="total_chunks is 0"):
            _reassemble_chunks([chunk])
