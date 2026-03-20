"""Protocol constants for Tuya LAN (WiFi) protocol.

These values are derived from publicly documented Tuya LAN protocol
specifications and open-source reference implementations.

References:
  - tinytuya: https://github.com/jasonacox/tinytuya
  - localtuya: https://github.com/rospogriern/localtuya
"""

from __future__ import annotations

# ── Wire framing ──────────────────────────────────────────────────────────────

#: 4-byte magic prefix on every Tuya LAN frame
FRAME_PREFIX: bytes = b"\x00\x00\x55\xaa"

#: 4-byte magic suffix on every Tuya LAN frame
FRAME_SUFFIX: bytes = b"\x00\x00\xaa\x55"

#: Header size: prefix(4) + seq(4) + cmd(4) + length(4) = 16 bytes
FRAME_HEADER_SIZE: int = 16  # bytes before payload
FRAME_SUFFIX_SIZE: int = 4

#: Maximum payload length accepted (prevents memory exhaustion)
MAX_PAYLOAD_SIZE: int = 65536  # 64 KiB

# ── Protocol versions ─────────────────────────────────────────────────────────

PROTOCOL_31: str = "3.1"
PROTOCOL_32: str = "3.2"
PROTOCOL_33: str = "3.3"
PROTOCOL_34: str = "3.4"
PROTOCOL_35: str = "3.5"

SUPPORTED_VERSIONS: frozenset[str] = frozenset(
    {PROTOCOL_31, PROTOCOL_32, PROTOCOL_33, PROTOCOL_34, PROTOCOL_35}
)

#: Versions that use AES-ECB (MD5-derived key)
VERSIONS_ECB: frozenset[str] = frozenset({PROTOCOL_31, PROTOCOL_32, PROTOCOL_33})

#: Versions that use AES-GCM (ECDH session key)
VERSIONS_GCM: frozenset[str] = frozenset({PROTOCOL_34, PROTOCOL_35})

#: v3.3 uses an ECB-encrypted payload with a 12-byte version header prepended.
#: v3.4/3.5 use AES-GCM (no ECB header) and return early in encrypt/decrypt_payload
#: before reaching the VERSIONS_WITH_PAYLOAD_HEADER check — so they are excluded here.
VERSIONS_WITH_PAYLOAD_HEADER: frozenset[str] = frozenset({PROTOCOL_33})

# ── Command codes ─────────────────────────────────────────────────────────────
# Note: 0x03-0x05 are session-key negotiation commands for v3.4/3.5.
# These overlap with legacy bind/unbind codes — use CMD_SESS_KEY_* exclusively.

#: UDP discovery broadcast (device → controller)
CMD_UDP: int = 0x00
#: AP-mode discovery
CMD_AP_CONFIG: int = 0x01
#: Activate device (fake-cloud mock uses this)
CMD_ACTIVE: int = 0x02
#: Session key negotiation start (v3.4/3.5): controller → device, carries X25519 public key
CMD_SESS_KEY_NEG_START: int = 0x03
#: Session key negotiation response (v3.4/3.5): device → controller, carries device public key
CMD_SESS_KEY_NEG_RESPONSE: int = 0x04
#: Session key negotiation finish (v3.4/3.5): controller → device, carries HMAC confirmation
CMD_SESS_KEY_NEG_FINISH: int = 0x05
#: Control command (set DPS values)
CMD_CONTROL: int = 0x07
#: Status query — request current DPS snapshot
CMD_STATUS: int = 0x08
#: Heartbeat / keepalive ping
CMD_HEARTBEAT: int = 0x09
#: Query / report DPS values (used by v3.5 extended messages)
CMD_DP_QUERY: int = 0x0A
#: Control command (new style, v3.4+)
CMD_CONTROL_NEW: int = 0x0D
#: DP report from device to controller
CMD_DP_REPORT: int = 0x12

# ── Network ports ─────────────────────────────────────────────────────────────

#: TCP port for LAN control
TCP_PORT: int = 6668

#: UDP port for discovery (unencrypted v3.1/3.2 broadcasts)
UDP_PORT: int = 6666

#: UDP port for encrypted discovery (v3.3+)
UDP_ENC_PORT: int = 6667

# ── Key derivation (ECB versions) ─────────────────────────────────────────────

#: Salt appended to local key before MD5 derivation (v3.1-3.3)
MD5_KEY_PREFIX: bytes = b"yGAdlopoPVldABfn"

#: Prefix embedded in v3.3+ encrypted payloads
V33_PAYLOAD_HEADER: bytes = b"3.3\x00\x00\x00\x00\x00\x00\x00\x00\x00"  # 12 bytes

# ── AES-GCM (v3.4/3.5) ────────────────────────────────────────────────────────

#: AES-GCM authentication tag size in bytes (full 128-bit GCM tag)
GCM_TAG_SIZE: int = 16

#: AES-GCM IV/nonce size in bytes
GCM_IV_SIZE: int = 12

# ── Timeouts ──────────────────────────────────────────────────────────────────

#: Default timeout for a TCP command/response round-trip (seconds)
DEFAULT_COMMAND_TIMEOUT: float = 5.0

#: Heartbeat interval (seconds)
HEARTBEAT_INTERVAL: float = 20.0

#: Reconnect back-off: initial, max (seconds)
RECONNECT_INITIAL_DELAY: float = 1.0
RECONNECT_MAX_DELAY: float = 60.0

#: UDP discovery socket read timeout (seconds)
UDP_READ_TIMEOUT: float = 5.0

#: TCP connect timeout (seconds)
TCP_CONNECT_TIMEOUT: float = 10.0

#: TCP receive timeout between chunks (seconds)
TCP_RECEIVE_TIMEOUT: float = 30.0

#: TCP read buffer size (bytes)
TCP_READ_BUFFER_SIZE: int = 4096

#: Discovery idle-poll interval when no devices seen yet (seconds)
DISCOVERY_POLLING_INTERVAL: float = 0.1

#: UDP discovery queue dequeue timeout (seconds)
UDP_QUEUE_TIMEOUT: float = 1.0

#: Age after which a discovered device is considered stale (seconds)
DISCOVERY_DEVICE_STALE_AGE: float = 300.0

#: Default timeout for wait_for_device (seconds)
DISCOVERY_WAIT_TIMEOUT: float = 30.0
