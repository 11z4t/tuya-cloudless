"""Unit tests for custom exceptions."""

from __future__ import annotations

from lib.tuya_cloudless.exceptions import (
    ConnectionError,
    CryptoError,
    DeviceNotFoundError,
    ProtocolError,
    TuyaCloudlessError,
)


def test_base_exception_hierarchy() -> None:
    """All custom exceptions should inherit from TuyaCloudlessError."""
    assert issubclass(ConnectionError, TuyaCloudlessError)
    assert issubclass(ProtocolError, TuyaCloudlessError)
    assert issubclass(CryptoError, TuyaCloudlessError)
    assert issubclass(DeviceNotFoundError, TuyaCloudlessError)


def test_exceptions_are_catchable() -> None:
    """Custom exceptions should be catchable as their base type."""
    try:
        raise ConnectionError("test")
    except TuyaCloudlessError as e:
        assert str(e) == "test"
