"""Exception hierarchy for Tuya Cloudless.

All exceptions inherit from ``TuyaCloudlessError``. Callers can catch the
base class for broad handling or specific subclasses for targeted recovery.

SECURITY: Exception messages MUST NEVER contain secret material
(keys, local keys, session IDs, passwords). Use lengths / operation names only.
"""

from __future__ import annotations


class TuyaCloudlessError(Exception):
    """Base exception for all Tuya Cloudless operations."""


# ── Connection ────────────────────────────────────────────────────────────────


class ConnectionError(TuyaCloudlessError):
    """Failed to establish or maintain a TCP connection to a device."""


class DeviceNotFoundError(TuyaCloudlessError):
    """Target device was not found during UDP discovery."""


class DeviceTimeoutError(TuyaCloudlessError):
    """Device did not respond within the allowed time."""


class DeviceUnavailableError(ConnectionError):
    """Operation attempted while the device is unreachable."""


# ── Protocol ──────────────────────────────────────────────────────────────────


class ProtocolError(TuyaCloudlessError):
    """Wire-level Tuya LAN protocol violation."""


class MalformedPacketError(ProtocolError):
    """Received packet failed structural validation (bad magic, length, CRC)."""


class UnsupportedVersionError(ProtocolError):
    """Protocol version in packet is not supported (outside 3.1–3.5)."""


class CommandError(ProtocolError):
    """Device returned an error response for a command."""


# ── Cryptography ──────────────────────────────────────────────────────────────


class CryptoError(TuyaCloudlessError):
    """Cryptographic operation failed."""


class AuthenticationError(CryptoError):
    """AES-GCM authentication tag verification failed (v3.4/3.5)."""


class KeyDerivationError(CryptoError):
    """Session key derivation (ECDH or MD5) failed."""


# ── Discovery ─────────────────────────────────────────────────────────────────


class DiscoveryError(TuyaCloudlessError):
    """UDP discovery subsystem encountered an unrecoverable error."""


# ── Pairing ───────────────────────────────────────────────────────────────────


class PairingError(TuyaCloudlessError):
    """BLE or fake-cloud device pairing failed."""


class ActivationError(PairingError):
    """Device activation via fake-cloud mock failed."""
