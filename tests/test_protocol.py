"""Tests for Tuya protocol parser."""

import pytest

from lib.tuya_cloudless.exceptions import TuyaProtocolError
from lib.tuya_cloudless.protocol import (
    COMMAND_CONTROL,
    COMMAND_DP_QUERY,
    COMMAND_HEARTBEAT,
    TuyaProtocolParser,
    create_control_message,
    create_dp_query_message,
    create_heartbeat_message,
)


class TestTuyaProtocolParser:
    """Test TuyaProtocolParser class."""

    def test_init_valid_versions(self) -> None:
        """Test initialization with valid protocol versions."""
        for version in ("3.1", "3.2", "3.3", "3.4", "3.5"):
            parser = TuyaProtocolParser(version)
            assert parser.protocol_version == version

    def test_init_invalid_version(self) -> None:
        """Test initialization with invalid protocol version."""
        with pytest.raises(TuyaProtocolError, match="Unsupported protocol version"):
            TuyaProtocolParser("2.0")

    def test_build_message_dict_payload(self) -> None:
        """Test building message with dict payload."""
        parser = TuyaProtocolParser("3.3")
        payload = {"test": "value"}
        msg = parser.build_message(COMMAND_CONTROL, payload, sequence_number=1)

        # Verify message structure
        assert len(msg) >= 24  # Minimum size
        assert msg[0:4] == b"\x00\x00\x55\xaa"  # Prefix
        assert msg[-4:] == b"\x00\x00\xaa\x55"  # Suffix

    def test_build_message_bytes_payload(self) -> None:
        """Test building message with bytes payload."""
        parser = TuyaProtocolParser("3.1")
        payload = b"test_payload"
        msg = parser.build_message(COMMAND_HEARTBEAT, payload, sequence_number=2)

        assert len(msg) >= 24
        assert msg[0:4] == b"\x00\x00\x55\xaa"

    def test_parse_message_roundtrip(self) -> None:
        """Test building and parsing message roundtrip."""
        parser = TuyaProtocolParser("3.3")
        original_payload = {"dps": {"1": True}}

        # Build message
        msg = parser.build_message(COMMAND_CONTROL, original_payload, sequence_number=5)

        # Parse message
        parsed = parser.parse_message(msg)

        assert parsed.command == COMMAND_CONTROL
        assert parsed.sequence_number == 5
        assert isinstance(parsed.payload, dict)

    def test_parse_message_too_short(self) -> None:
        """Test parsing message that is too short."""
        parser = TuyaProtocolParser("3.3")
        with pytest.raises(TuyaProtocolError, match="Message too short"):
            parser.parse_message(b"short")

    def test_parse_message_invalid_prefix(self) -> None:
        """Test parsing message with invalid prefix."""
        parser = TuyaProtocolParser("3.3")
        # Create message with wrong prefix
        invalid_msg = b"\x00\x00\x00\x00" + b"\x00" * 20

        with pytest.raises(TuyaProtocolError, match="Invalid prefix"):
            parser.parse_message(invalid_msg)

    def test_create_dp_query_v31(self) -> None:
        """Test creating DP query message for v3.1."""
        msg = create_dp_query_message(sequence_number=10, protocol_version="3.1")

        parser = TuyaProtocolParser("3.1")
        parsed = parser.parse_message(msg)

        assert parsed.command == COMMAND_DP_QUERY
        assert parsed.sequence_number == 10

    def test_create_control_message(self) -> None:
        """Test creating control message."""
        dps = {"1": True, "2": 255}
        msg = create_control_message(dps, sequence_number=15, protocol_version="3.3")

        parser = TuyaProtocolParser("3.3")
        parsed = parser.parse_message(msg)

        assert parsed.command == COMMAND_CONTROL
        assert parsed.sequence_number == 15
        assert isinstance(parsed.payload, dict)
        assert "dps" in parsed.payload

    def test_create_heartbeat_message(self) -> None:
        """Test creating heartbeat message."""
        msg = create_heartbeat_message(sequence_number=20, protocol_version="3.3")

        parser = TuyaProtocolParser("3.3")
        parsed = parser.parse_message(msg)

        assert parsed.command == COMMAND_HEARTBEAT
        assert parsed.sequence_number == 20

    def test_crc_validation(self) -> None:
        """Test CRC validation catches corruption."""
        parser = TuyaProtocolParser("3.3")
        msg = parser.build_message(COMMAND_HEARTBEAT, b"", sequence_number=0)

        # Corrupt message (flip bit in payload area)
        corrupted = bytearray(msg)
        corrupted[20] ^= 0xFF
        corrupted_msg = bytes(corrupted)

        with pytest.raises(TuyaProtocolError, match="CRC mismatch"):
            parser.parse_message(corrupted_msg)

    def test_protocol_version_detection_v33(self) -> None:
        """Test protocol version detection for v3.3."""
        parser = TuyaProtocolParser("3.3")
        msg = parser.build_message(COMMAND_CONTROL, {"test": True})
        parsed = parser.parse_message(msg)

        assert parsed.protocol_version in ("3.3", "3.5")  # v3.5 uses same format

    def test_protocol_version_detection_v34(self) -> None:
        """Test protocol version detection for v3.4."""
        parser = TuyaProtocolParser("3.4")
        msg = parser.build_message(COMMAND_CONTROL, {"test": True})
        parsed = parser.parse_message(msg)

        assert parsed.protocol_version == "3.4"

    def test_empty_payload(self) -> None:
        """Test handling empty payload."""
        parser = TuyaProtocolParser("3.1")
        msg = parser.build_message(COMMAND_HEARTBEAT, b"")
        parsed = parser.parse_message(msg)

        assert parsed.command == COMMAND_HEARTBEAT
        assert parsed.payload == b"" or parsed.payload == {}

    def test_large_payload(self) -> None:
        """Test handling large payload."""
        parser = TuyaProtocolParser("3.3")
        large_payload = {"data": "x" * 1000}
        msg = parser.build_message(COMMAND_CONTROL, large_payload)
        parsed = parser.parse_message(msg)

        assert isinstance(parsed.payload, dict)
        assert "data" in parsed.payload
