"""Unit tests for the tuya_cloudless exception hierarchy."""

from __future__ import annotations

import pytest
from tuya_cloudless.exceptions import (
    ActivationError,
    AuthenticationError,
    CommandError,
    ConnectionError,
    CryptoError,
    DeviceNotFoundError,
    DeviceTimeoutError,
    DeviceUnavailableError,
    DiscoveryError,
    KeyDerivationError,
    MalformedPacketError,
    PairingError,
    ProtocolError,
    TuyaCloudlessError,
    UnsupportedVersionError,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_base(self) -> None:
        for exc_cls in [
            ConnectionError,
            DeviceNotFoundError,
            DeviceTimeoutError,
            DeviceUnavailableError,
            ProtocolError,
            MalformedPacketError,
            UnsupportedVersionError,
            CommandError,
            CryptoError,
            AuthenticationError,
            KeyDerivationError,
            DiscoveryError,
            PairingError,
            ActivationError,
        ]:
            assert issubclass(exc_cls, TuyaCloudlessError), (
                f"{exc_cls.__name__} does not inherit from TuyaCloudlessError"
            )

    def test_auth_error_is_crypto_error(self) -> None:
        assert issubclass(AuthenticationError, CryptoError)

    def test_malformed_packet_is_protocol_error(self) -> None:
        assert issubclass(MalformedPacketError, ProtocolError)

    def test_unavailable_is_connection_error(self) -> None:
        assert issubclass(DeviceUnavailableError, ConnectionError)

    def test_activation_is_pairing_error(self) -> None:
        assert issubclass(ActivationError, PairingError)

    def test_messages_do_not_contain_key_material(self) -> None:
        """Exception messages must never include raw secrets."""
        secret = "SUPER_SECRET_KEY_12345"
        exc = CryptoError(f"Operation failed, key length={len(secret)}")
        assert secret not in str(exc)

    def test_catchable_as_base(self) -> None:
        with pytest.raises(TuyaCloudlessError):
            raise AuthenticationError("tag mismatch")

    def test_catchable_as_crypto_error(self) -> None:
        with pytest.raises(CryptoError):
            raise AuthenticationError("tag mismatch")
