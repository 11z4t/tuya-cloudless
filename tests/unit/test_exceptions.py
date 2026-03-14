"""Unit tests for the Tuya Cloudless exception hierarchy.

Tests cover:
- Complete inheritance tree validation
- Constructor behavior (message, device_id)
- String representation with and without device_id
- Catchability at every level of the hierarchy
- __all__ export completeness
- Exception attribute preservation through re-raise
"""

from __future__ import annotations

import pytest

from lib.tuya_cloudless import exceptions as exc_module
from lib.tuya_cloudless.exceptions import (
    ConnectionError,
    ConnectionRefusedError,
    ConnectionTimeoutError,
    CryptoError,
    DecryptionError,
    DeviceNotFoundError,
    EncryptionError,
    FakeCloudActivationError,
    FakeCloudDNSError,
    FakeCloudError,
    FakeCloudSessionError,
    FakeCloudTLSError,
    InvalidMessageError,
    PairingAlreadyRegisteredError,
    PairingDeviceRefusedError,
    PairingError,
    PairingTimeoutError,
    PairingTokenError,
    ProtocolError,
    ProtocolVersionError,
    SequenceError,
    TuyaCloudlessError,
    WebServerAuthError,
    WebServerBindError,
    WebServerError,
    WebServerRequestError,
    WebServerRouteError,
    WiFiAuthenticationError,
    WiFiConnectError,
    WiFiError,
    WiFiNetworkLostError,
    WiFiScanError,
)

# ---------------------------------------------------------------------------
# Hierarchy structure: (child, parent) pairs
# ---------------------------------------------------------------------------

_HIERARCHY: list[tuple[type[TuyaCloudlessError], type[Exception]]] = [
    # Top-level
    (TuyaCloudlessError, Exception),
    # Connection subtree
    (ConnectionError, TuyaCloudlessError),
    (ConnectionTimeoutError, ConnectionError),
    (ConnectionRefusedError, ConnectionError),
    # Protocol subtree
    (ProtocolError, TuyaCloudlessError),
    (ProtocolVersionError, ProtocolError),
    (InvalidMessageError, ProtocolError),
    (SequenceError, ProtocolError),
    # Crypto subtree
    (CryptoError, TuyaCloudlessError),
    (EncryptionError, CryptoError),
    (DecryptionError, CryptoError),
    # Discovery
    (DeviceNotFoundError, TuyaCloudlessError),
    # WiFi subtree
    (WiFiError, TuyaCloudlessError),
    (WiFiScanError, WiFiError),
    (WiFiConnectError, WiFiError),
    (WiFiAuthenticationError, WiFiError),
    (WiFiNetworkLostError, WiFiError),
    # Pairing subtree
    (PairingError, TuyaCloudlessError),
    (PairingTimeoutError, PairingError),
    (PairingTokenError, PairingError),
    (PairingDeviceRefusedError, PairingError),
    (PairingAlreadyRegisteredError, PairingError),
    # Fake cloud subtree
    (FakeCloudError, TuyaCloudlessError),
    (FakeCloudDNSError, FakeCloudError),
    (FakeCloudTLSError, FakeCloudError),
    (FakeCloudActivationError, FakeCloudError),
    (FakeCloudSessionError, FakeCloudError),
    # Web server subtree
    (WebServerError, TuyaCloudlessError),
    (WebServerBindError, WebServerError),
    (WebServerRouteError, WebServerError),
    (WebServerAuthError, WebServerError),
    (WebServerRequestError, WebServerError),
]

# All concrete exception classes (excludes the base Exception)
_ALL_EXCEPTIONS: list[type[TuyaCloudlessError]] = [child for child, _ in _HIERARCHY]


# ---------------------------------------------------------------------------
# Inheritance tests
# ---------------------------------------------------------------------------


class TestHierarchyStructure:
    """Verify the complete inheritance tree."""

    @pytest.mark.parametrize(
        ("child", "parent"),
        _HIERARCHY,
        ids=[f"{c.__name__} -> {p.__name__}" for c, p in _HIERARCHY],
    )
    def test_subclass_relationship(self, child: type[Exception], parent: type[Exception]) -> None:
        """Each exception must be a direct subclass of its parent."""
        assert issubclass(child, parent)

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_all_inherit_from_base(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """Every exception in the tree inherits from TuyaCloudlessError."""
        assert issubclass(exc_cls, TuyaCloudlessError)

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_all_inherit_from_exception(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """Every exception in the tree inherits from built-in Exception."""
        assert issubclass(exc_cls, Exception)


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestConstructor:
    """Verify constructor behavior and attributes."""

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_message_only(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """Constructing with just a message sets message and device_id=None."""
        err = exc_cls("something failed")
        assert err.message == "something failed"
        assert err.device_id is None

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_message_with_device_id(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """Constructing with device_id sets both attributes."""
        err = exc_cls("bad", device_id="bf12345678abcdef")
        assert err.message == "bad"
        assert err.device_id == "bf12345678abcdef"

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_default_device_id_is_none(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """device_id defaults to None when not provided."""
        err = exc_cls("msg")
        assert err.device_id is None


# ---------------------------------------------------------------------------
# String representation tests
# ---------------------------------------------------------------------------


class TestStringRepresentation:
    """Verify str() output format."""

    def test_str_without_device_id(self) -> None:
        """Without device_id, str(err) is just the message."""
        err = TuyaCloudlessError("plain message")
        assert str(err) == "plain message"

    def test_str_with_device_id(self) -> None:
        """With device_id, str(err) includes the device_id prefix."""
        err = TuyaCloudlessError("something broke", device_id="abc123")
        assert str(err) == "[abc123] something broke"

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_str_format_no_device(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """All subclasses produce plain message without device_id."""
        err = exc_cls("test msg")
        assert str(err) == "test msg"

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_str_format_with_device(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """All subclasses produce '[device_id] message' format."""
        err = exc_cls("test msg", device_id="dev99")
        assert str(err) == "[dev99] test msg"


# ---------------------------------------------------------------------------
# Catchability tests
# ---------------------------------------------------------------------------


class TestCatchability:
    """Verify exceptions can be caught at multiple hierarchy levels."""

    def test_catch_leaf_as_root(self) -> None:
        """A leaf exception is catchable as TuyaCloudlessError."""
        with pytest.raises(TuyaCloudlessError):
            raise WiFiAuthenticationError("bad pw")

    def test_catch_leaf_as_mid(self) -> None:
        """A leaf exception is catchable as its mid-level parent."""
        with pytest.raises(WiFiError):
            raise WiFiAuthenticationError("bad pw")

    def test_catch_leaf_as_self(self) -> None:
        """A leaf exception is catchable as its own type."""
        with pytest.raises(WiFiAuthenticationError):
            raise WiFiAuthenticationError("bad pw")

    def test_catch_connection_subtree(self) -> None:
        """ConnectionTimeoutError is catchable as ConnectionError."""
        with pytest.raises(ConnectionError):
            raise ConnectionTimeoutError("timed out")

    def test_catch_protocol_subtree(self) -> None:
        """InvalidMessageError is catchable as ProtocolError."""
        with pytest.raises(ProtocolError):
            raise InvalidMessageError("bad header")

    def test_catch_crypto_subtree(self) -> None:
        """DecryptionError is catchable as CryptoError."""
        with pytest.raises(CryptoError):
            raise DecryptionError("wrong key")

    def test_catch_pairing_subtree(self) -> None:
        """PairingTokenError is catchable as PairingError."""
        with pytest.raises(PairingError):
            raise PairingTokenError("expired token")

    def test_catch_fakecloud_subtree(self) -> None:
        """FakeCloudTLSError is catchable as FakeCloudError."""
        with pytest.raises(FakeCloudError):
            raise FakeCloudTLSError("cert gen failed")

    def test_catch_webserver_subtree(self) -> None:
        """WebServerAuthError is catchable as WebServerError."""
        with pytest.raises(WebServerError):
            raise WebServerAuthError("invalid token")

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_all_catchable_as_base(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """Every exception is catchable as TuyaCloudlessError."""
        with pytest.raises(TuyaCloudlessError):
            raise exc_cls("test")


# ---------------------------------------------------------------------------
# Attribute preservation through re-raise
# ---------------------------------------------------------------------------


class TestAttributePreservation:
    """Ensure attributes survive catch-and-reraise patterns."""

    def test_attributes_preserved_after_reraise(self) -> None:
        """message and device_id survive a catch-and-reraise."""
        try:
            raise ConnectionTimeoutError("slow device", device_id="bf001122")
        except ConnectionError as first:
            try:
                raise ConnectionTimeoutError(first.message, device_id=first.device_id) from first
            except TuyaCloudlessError as second:
                assert second.message == "slow device"
                assert second.device_id == "bf001122"
                assert second.__cause__ is first

    def test_chained_exception(self) -> None:
        """Exceptions can be chained with __cause__."""
        original = DecryptionError("bad padding")
        wrapper = ProtocolError("decode failed", device_id="d1")
        wrapper.__cause__ = original
        assert wrapper.__cause__ is original
        assert wrapper.device_id == "d1"


# ---------------------------------------------------------------------------
# __all__ export validation
# ---------------------------------------------------------------------------


class TestExports:
    """Validate the __all__ list is complete and correct."""

    def test_all_exceptions_in_module_all(self) -> None:
        """Every exception class must be listed in __all__."""
        module_all = set(exc_module.__all__)
        for exc_cls in _ALL_EXCEPTIONS:
            assert exc_cls.__name__ in module_all, f"{exc_cls.__name__} missing from __all__"

    def test_all_entries_are_exception_classes(self) -> None:
        """Every name in __all__ must resolve to an exception class."""
        for name in exc_module.__all__:
            obj = getattr(exc_module, name)
            assert isinstance(obj, type), f"{name} is not a class"
            assert issubclass(obj, TuyaCloudlessError), (
                f"{name} does not inherit from TuyaCloudlessError"
            )

    def test_all_count_matches_hierarchy(self) -> None:
        """__all__ has exactly as many entries as the hierarchy definition."""
        assert len(exc_module.__all__) == len(_ALL_EXCEPTIONS)


# ---------------------------------------------------------------------------
# Docstring validation
# ---------------------------------------------------------------------------


class TestDocstrings:
    """Ensure every exception class has a docstring."""

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTIONS, ids=lambda c: c.__name__)
    def test_has_docstring(self, exc_cls: type[TuyaCloudlessError]) -> None:
        """Every exception must have a non-empty docstring."""
        assert exc_cls.__doc__ is not None
        assert len(exc_cls.__doc__.strip()) > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and defensive checks."""

    def test_empty_message(self) -> None:
        """Empty message string is accepted."""
        err = TuyaCloudlessError("")
        assert err.message == ""
        assert str(err) == ""

    def test_empty_message_with_device_id(self) -> None:
        """Empty message with device_id still formats correctly."""
        err = TuyaCloudlessError("", device_id="x")
        assert str(err) == "[x] "

    def test_unicode_message(self) -> None:
        """Unicode characters in message are preserved."""
        err = WiFiError("nätverksfel: enheten svarar inte")
        assert "nätverksfel" in str(err)

    def test_long_device_id(self) -> None:
        """Long device IDs are handled without truncation."""
        long_id = "a" * 200
        err = DeviceNotFoundError("gone", device_id=long_id)
        assert err.device_id == long_id
        assert long_id in str(err)

    def test_not_catchable_as_wrong_subtree(self) -> None:
        """A WiFi exception must NOT be catchable as a PairingError."""
        with pytest.raises(WiFiError):
            try:
                raise WiFiScanError("no networks")
            except PairingError:
                pytest.fail("WiFiScanError should not be caught as PairingError")

    def test_isinstance_checks(self) -> None:
        """isinstance works correctly across the tree."""
        err = FakeCloudDNSError("dns failed", device_id="fc1")
        assert isinstance(err, FakeCloudDNSError)
        assert isinstance(err, FakeCloudError)
        assert isinstance(err, TuyaCloudlessError)
        assert isinstance(err, Exception)
        assert not isinstance(err, WebServerError)
        assert not isinstance(err, WiFiError)
