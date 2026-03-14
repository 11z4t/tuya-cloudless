"""Exception hierarchy for Tuya Cloudless.

Provides a structured tree of exceptions for all subsystems:
WiFi communication, device pairing, fake cloud server, and web server.

Hierarchy::

    TuyaCloudlessError
    ├── ConnectionError
    │   ├── ConnectionTimeoutError
    │   └── ConnectionRefusedError
    ├── ProtocolError
    │   ├── ProtocolVersionError
    │   ├── InvalidMessageError
    │   └── SequenceError
    ├── CryptoError
    │   ├── EncryptionError
    │   └── DecryptionError
    ├── DeviceNotFoundError
    ├── WiFiError
    │   ├── WiFiScanError
    │   ├── WiFiConnectError
    │   ├── WiFiAuthenticationError
    │   └── WiFiNetworkLostError
    ├── PairingError
    │   ├── PairingTimeoutError
    │   ├── PairingTokenError
    │   ├── PairingDeviceRefusedError
    │   └── PairingAlreadyRegisteredError
    ├── FakeCloudError
    │   ├── FakeCloudDNSError
    │   ├── FakeCloudTLSError
    │   ├── FakeCloudActivationError
    │   └── FakeCloudSessionError
    └── WebServerError
        ├── WebServerBindError
        ├── WebServerRouteError
        ├── WebServerAuthError
        └── WebServerRequestError
"""

from __future__ import annotations


class TuyaCloudlessError(Exception):
    """Base exception for all Tuya Cloudless operations.

    Every exception raised by this library inherits from this class,
    allowing callers to catch all library errors with a single handler.

    Attributes:
        message: Human-readable error description.
        device_id: Optional device identifier for context.
    """

    def __init__(self, message: str, *, device_id: str | None = None) -> None:
        self.message = message
        self.device_id = device_id
        if device_id is not None:
            super().__init__(f"[{device_id}] {message}")
        else:
            super().__init__(message)


# ---------------------------------------------------------------------------
# Connection errors — TCP/UDP communication with devices
# ---------------------------------------------------------------------------


class ConnectionError(TuyaCloudlessError):
    """Failed to establish or maintain a TCP connection to a Tuya device."""


class ConnectionTimeoutError(ConnectionError):
    """Connection attempt timed out before the device responded."""


class ConnectionRefusedError(ConnectionError):
    """Device actively refused the connection attempt."""


# ---------------------------------------------------------------------------
# Protocol errors — Tuya Local protocol message handling
# ---------------------------------------------------------------------------


class ProtocolError(TuyaCloudlessError):
    """Error in Tuya Local protocol message handling."""


class ProtocolVersionError(ProtocolError):
    """Device protocol version is unsupported (not v3.1/v3.3/v3.4/v3.5)."""


class InvalidMessageError(ProtocolError):
    """Received message has invalid header, CRC, or structure."""


class SequenceError(ProtocolError):
    """Message sequence number is out of order or duplicated."""


# ---------------------------------------------------------------------------
# Crypto errors — Encryption/decryption operations
# ---------------------------------------------------------------------------


class CryptoError(TuyaCloudlessError):
    """Cryptographic operation failed during protocol communication."""


class EncryptionError(CryptoError):
    """Payload encryption failed (bad key length, invalid data)."""


class DecryptionError(CryptoError):
    """Payload decryption failed (wrong key, corrupted ciphertext, bad padding)."""


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------


class DeviceNotFoundError(TuyaCloudlessError):
    """Device was not found on the local network via UDP broadcast discovery."""


# ---------------------------------------------------------------------------
# WiFi errors — Station-mode WiFi operations for device setup
# ---------------------------------------------------------------------------


class WiFiError(TuyaCloudlessError):
    """Error during WiFi operations for device provisioning."""


class WiFiScanError(WiFiError):
    """Failed to scan for available WiFi networks."""


class WiFiConnectError(WiFiError):
    """Failed to connect to the device's AP or target network."""


class WiFiAuthenticationError(WiFiError):
    """WiFi authentication failed (wrong password or unsupported security)."""


class WiFiNetworkLostError(WiFiError):
    """Lost connection to the WiFi network during an active operation."""


# ---------------------------------------------------------------------------
# Pairing errors — Device registration and key exchange
# ---------------------------------------------------------------------------


class PairingError(TuyaCloudlessError):
    """Error during the device pairing / registration flow."""


class PairingTimeoutError(PairingError):
    """Pairing handshake did not complete within the expected time."""


class PairingTokenError(PairingError):
    """Pairing token is invalid, expired, or rejected by the device."""


class PairingDeviceRefusedError(PairingError):
    """Device explicitly refused the pairing request."""


class PairingAlreadyRegisteredError(PairingError):
    """Device is already registered and must be factory-reset before re-pairing."""


# ---------------------------------------------------------------------------
# Fake cloud errors — Local mock of Tuya cloud API for activation
# ---------------------------------------------------------------------------


class FakeCloudError(TuyaCloudlessError):
    """Error in the fake cloud server that intercepts Tuya cloud calls."""


class FakeCloudDNSError(FakeCloudError):
    """Failed to redirect DNS queries for Tuya cloud domains."""


class FakeCloudTLSError(FakeCloudError):
    """TLS certificate generation or handshake failed for fake cloud endpoint."""


class FakeCloudActivationError(FakeCloudError):
    """Device activation request handling failed in the fake cloud."""


class FakeCloudSessionError(FakeCloudError):
    """Fake cloud session state is invalid or expired."""


# ---------------------------------------------------------------------------
# Web server errors — Local management/configuration web UI
# ---------------------------------------------------------------------------


class WebServerError(TuyaCloudlessError):
    """Error in the local management web server."""


class WebServerBindError(WebServerError):
    """Web server failed to bind to the requested address/port."""


class WebServerRouteError(WebServerError):
    """Requested route or endpoint does not exist."""


class WebServerAuthError(WebServerError):
    """Authentication or authorization failed for a web server request."""


class WebServerRequestError(WebServerError):
    """Invalid or malformed request received by the web server."""


# ---------------------------------------------------------------------------
# Public API — all exceptions available for import
# ---------------------------------------------------------------------------

__all__ = [
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionTimeoutError",
    "CryptoError",
    "DecryptionError",
    "DeviceNotFoundError",
    "EncryptionError",
    "FakeCloudActivationError",
    "FakeCloudDNSError",
    "FakeCloudError",
    "FakeCloudSessionError",
    "FakeCloudTLSError",
    "InvalidMessageError",
    "PairingAlreadyRegisteredError",
    "PairingDeviceRefusedError",
    "PairingError",
    "PairingTimeoutError",
    "PairingTokenError",
    "ProtocolError",
    "ProtocolVersionError",
    "SequenceError",
    "TuyaCloudlessError",
    "WebServerAuthError",
    "WebServerBindError",
    "WebServerError",
    "WebServerRequestError",
    "WebServerRouteError",
    "WiFiAuthenticationError",
    "WiFiConnectError",
    "WiFiError",
    "WiFiNetworkLostError",
    "WiFiScanError",
]
