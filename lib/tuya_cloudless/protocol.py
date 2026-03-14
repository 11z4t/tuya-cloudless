"""Tuya protocol parser — supports v3.1, v3.2, v3.3, v3.4, v3.5."""

import json
import struct
from typing import Any

from .exceptions import TuyaProtocolError

# Protocol constants
PROTOCOL_VERSION_BYTES_31 = b"3.1"
PROTOCOL_VERSION_BYTES_33 = b"3.3"
PROTOCOL_VERSION_BYTES_34 = b"3.4"

PREFIX_VALUE = 0x000055AA
SUFFIX_VALUE = 0x0000AA55

# Command types
COMMAND_DP_QUERY = 0x0A  # Query device status
COMMAND_DP_QUERY_NEW = 0x10  # Query status (v3.4+)
COMMAND_CONTROL = 0x07  # Control device
COMMAND_STATUS = 0x08  # Status report
COMMAND_HEARTBEAT = 0x09  # Heartbeat
COMMAND_DP_REFRESH = 0x12  # Refresh DPs


class TuyaMessage:
    """Represents a Tuya protocol message."""

    def __init__(
        self,
        command: int,
        payload: dict[str, Any] | bytes,
        sequence_number: int = 0,
        protocol_version: str = "3.3",
    ) -> None:
        """Initialize a Tuya message.

        Args:
            command: Command type (0x07, 0x0A, etc.)
            payload: Message payload (dict or bytes)
            sequence_number: Message sequence number
            protocol_version: Protocol version ("3.1", "3.2", "3.3", "3.4", "3.5")
        """
        self.command = command
        self.payload = payload
        self.sequence_number = sequence_number
        self.protocol_version = protocol_version

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary representation."""
        return {
            "command": self.command,
            "payload": self.payload if isinstance(self.payload, dict) else self.payload.hex(),
            "sequence_number": self.sequence_number,
            "protocol_version": self.protocol_version,
        }


class TuyaProtocolParser:
    """Parser for Tuya protocol messages (v3.1-3.5)."""

    def __init__(self, protocol_version: str = "3.3") -> None:
        """Initialize protocol parser.

        Args:
            protocol_version: Protocol version ("3.1", "3.2", "3.3", "3.4", "3.5")
        """
        if protocol_version not in ("3.1", "3.2", "3.3", "3.4", "3.5"):
            raise TuyaProtocolError(f"Unsupported protocol version: {protocol_version}")

        self.protocol_version = protocol_version

    def build_message(
        self,
        command: int,
        payload: dict[str, Any] | bytes = b"",
        sequence_number: int = 0,
    ) -> bytes:
        """Build a Tuya protocol message.

        Args:
            command: Command type
            payload: Message payload (dict will be JSON-encoded)
            sequence_number: Message sequence number

        Returns:
            Raw message bytes ready for transmission

        Message format:
            [PREFIX(4)] [SEQ(4)] [CMD(4)] [LEN(4)] [PAYLOAD] [CRC(4)] [SUFFIX(4)]
        """
        # Encode payload
        if isinstance(payload, dict):
            payload_bytes = json.dumps(payload).encode("utf-8")
        else:
            payload_bytes = payload

        # Add protocol version prefix for 3.3+
        if self.protocol_version in ("3.3", "3.4", "3.5"):
            # v3.3+ includes version in payload
            version_header = PROTOCOL_VERSION_BYTES_33
            if self.protocol_version == "3.4":
                version_header = PROTOCOL_VERSION_BYTES_34

            payload_bytes = version_header + payload_bytes

        # Build message header
        prefix = struct.pack(">I", PREFIX_VALUE)
        seq = struct.pack(">I", sequence_number)
        cmd = struct.pack(">I", command)
        payload_len = len(payload_bytes)

        # Calculate total length (includes payload + CRC + suffix)
        total_len = payload_len + 4 + 4  # payload + CRC(4) + suffix(4)
        length_field = struct.pack(">I", total_len)

        # Build message without CRC
        message = prefix + seq + cmd + length_field + payload_bytes

        # Calculate and append CRC
        crc = self._calculate_crc(message)
        crc_bytes = struct.pack(">I", crc)

        # Append suffix
        suffix = struct.pack(">I", SUFFIX_VALUE)

        return message + crc_bytes + suffix

    def parse_message(self, data: bytes) -> TuyaMessage:
        """Parse a raw Tuya protocol message.

        Args:
            data: Raw message bytes

        Returns:
            Parsed TuyaMessage object

        Raises:
            TuyaProtocolError: If message is invalid or corrupt
        """
        if len(data) < 24:  # Minimum message size
            raise TuyaProtocolError(f"Message too short: {len(data)} bytes")

        # Parse header
        try:
            prefix = struct.unpack(">I", data[0:4])[0]
            seq = struct.unpack(">I", data[4:8])[0]
            cmd = struct.unpack(">I", data[8:12])[0]
            payload_len = struct.unpack(">I", data[12:16])[0]
        except struct.error as e:
            raise TuyaProtocolError(f"Failed to parse header: {e}") from e

        # Validate prefix
        if prefix != PREFIX_VALUE:
            raise TuyaProtocolError(f"Invalid prefix: 0x{prefix:08X}")

        # Extract payload (length includes CRC and suffix)
        payload_end = 16 + payload_len - 8  # -8 for CRC and suffix
        if payload_end > len(data):
            raise TuyaProtocolError(f"Payload length exceeds message size: {payload_len}")

        payload_bytes = data[16:payload_end]

        # Verify CRC
        crc_expected = struct.unpack(">I", data[payload_end : payload_end + 4])[0]
        crc_actual = self._calculate_crc(data[:payload_end])

        if crc_expected != crc_actual:
            raise TuyaProtocolError(
                f"CRC mismatch: expected 0x{crc_expected:08X}, got 0x{crc_actual:08X}"
            )

        # Verify suffix
        suffix = struct.unpack(">I", data[payload_end + 4 : payload_end + 8])[0]
        if suffix != SUFFIX_VALUE:
            raise TuyaProtocolError(f"Invalid suffix: 0x{suffix:08X}")

        # Detect protocol version from payload
        protocol_version = self._detect_protocol_version(payload_bytes)

        # Parse payload
        payload = self._parse_payload(payload_bytes, protocol_version)

        return TuyaMessage(
            command=cmd,
            payload=payload,
            sequence_number=seq,
            protocol_version=protocol_version,
        )

    def _detect_protocol_version(self, payload: bytes) -> str:
        """Detect protocol version from payload prefix.

        Args:
            payload: Payload bytes

        Returns:
            Protocol version string ("3.1", "3.2", "3.3", "3.4", "3.5")
        """
        if len(payload) < 3:
            return self.protocol_version  # Default to parser version

        # v3.4 check
        if payload[:3] == PROTOCOL_VERSION_BYTES_34:
            return "3.4"

        # v3.3 check
        if payload[:3] == PROTOCOL_VERSION_BYTES_33:
            # v3.5 is same as v3.3 with different crypto (detected at crypto layer)
            return self.protocol_version if self.protocol_version == "3.5" else "3.3"

        # v3.1 check
        if payload[:3] == PROTOCOL_VERSION_BYTES_31:
            return "3.1"

        # No version prefix = v3.1 or v3.2
        return "3.1"

    def _parse_payload(self, payload: bytes, protocol_version: str) -> dict[str, Any] | bytes:
        """Parse message payload.

        Args:
            payload: Raw payload bytes
            protocol_version: Detected protocol version

        Returns:
            Parsed payload (dict if JSON, bytes otherwise)
        """
        # Strip version prefix for v3.3+
        if protocol_version in ("3.3", "3.4", "3.5") and len(payload) >= 3:
            # Check for version header
            if payload[:3] in (PROTOCOL_VERSION_BYTES_33, PROTOCOL_VERSION_BYTES_34):
                payload = payload[3:]

        # Try to parse as JSON
        try:
            result: dict[str, Any] = json.loads(payload.decode("utf-8"))
            return result
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Return raw bytes if not JSON
            return payload

    def _calculate_crc(self, data: bytes) -> int:
        """Calculate CRC-32 checksum.

        Args:
            data: Data to checksum

        Returns:
            CRC-32 value
        """
        import binascii

        return binascii.crc32(data) & 0xFFFFFFFF


def create_dp_query_message(sequence_number: int = 0, protocol_version: str = "3.3") -> bytes:
    """Create a DP query message.

    Args:
        sequence_number: Message sequence number
        protocol_version: Protocol version

    Returns:
        Raw message bytes
    """
    parser = TuyaProtocolParser(protocol_version)
    command = COMMAND_DP_QUERY_NEW if protocol_version in ("3.4", "3.5") else COMMAND_DP_QUERY
    return parser.build_message(command, {"gwId": "", "devId": ""}, sequence_number)


def create_control_message(
    dps: dict[str, Any], sequence_number: int = 0, protocol_version: str = "3.3"
) -> bytes:
    """Create a control message.

    Args:
        dps: Data points to set (e.g., {"1": True, "2": 255})
        sequence_number: Message sequence number
        protocol_version: Protocol version

    Returns:
        Raw message bytes
    """
    parser = TuyaProtocolParser(protocol_version)
    payload = {"devId": "", "gwId": "", "dps": dps, "t": 0, "uid": ""}
    return parser.build_message(COMMAND_CONTROL, payload, sequence_number)


def create_heartbeat_message(sequence_number: int = 0, protocol_version: str = "3.3") -> bytes:
    """Create a heartbeat message.

    Args:
        sequence_number: Message sequence number
        protocol_version: Protocol version

    Returns:
        Raw message bytes
    """
    parser = TuyaProtocolParser(protocol_version)
    return parser.build_message(COMMAND_HEARTBEAT, b"", sequence_number)
