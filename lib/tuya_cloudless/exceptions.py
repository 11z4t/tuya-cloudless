"""Custom exceptions for Tuya Cloudless."""

from __future__ import annotations


class TuyaCloudlessError(Exception):
    """Base exception for Tuya Cloudless."""


class ConnectionError(TuyaCloudlessError):
    """Failed to connect to device."""


class ProtocolError(TuyaCloudlessError):
    """Protocol-level error in communication."""


class CryptoError(TuyaCloudlessError):
    """Cryptographic operation failed."""


class DeviceNotFoundError(TuyaCloudlessError):
    """Device not found on the network."""
