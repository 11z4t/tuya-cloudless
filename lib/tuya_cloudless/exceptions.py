"""Custom exceptions for Tuya Cloudless library."""


class TuyaCloudlessError(Exception):
    """Base exception for all Tuya Cloudless errors."""

    pass


class TuyaConnectionError(TuyaCloudlessError):
    """Raised when connection to Tuya device fails."""

    pass


class TuyaProtocolError(TuyaCloudlessError):
    """Raised when protocol parsing or validation fails."""

    pass


class TuyaCryptoError(TuyaCloudlessError):
    """Raised when encryption/decryption operations fail."""

    pass


class TuyaDiscoveryError(TuyaCloudlessError):
    """Raised when device discovery fails."""

    pass
