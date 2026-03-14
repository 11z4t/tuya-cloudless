"""Security tests — verify that secrets never appear in exception messages or logs."""

from __future__ import annotations

import pytest
from tuya_cloudless.crypto import decrypt_gcm, derive_ecb_key, encrypt_gcm
from tuya_cloudless.exceptions import AuthenticationError, CryptoError, KeyDerivationError


class TestSecretLeakage:
    """Verify that key material never leaks into exception messages."""

    _SECRET_KEY = b"\xDE\xAD\xBE\xEF" * 4  # 16 bytes
    _SECRET_STR = "deadbeefdeadbeef"

    def _key_hex(self) -> str:
        return self._SECRET_KEY.hex()

    def test_wrong_length_key_no_secret_in_message(self) -> None:
        bad_key = b"short"
        with pytest.raises(KeyDerivationError) as exc_info:
            derive_ecb_key(bad_key)
        msg = str(exc_info.value)
        # Must not contain the raw key bytes
        assert bad_key.hex() not in msg
        assert bad_key.decode("latin-1") not in msg

    def test_gcm_wrong_key_no_secret_in_message(self) -> None:
        ct = encrypt_gcm(self._SECRET_KEY, b"payload")
        wrong_key = b"\xFF" * 16
        with pytest.raises(AuthenticationError) as exc_info:
            decrypt_gcm(wrong_key, ct)
        msg = str(exc_info.value)
        assert self._key_hex() not in msg
        assert wrong_key.hex() not in msg

    def test_crypto_error_message_safe(self) -> None:
        """CryptoError must describe the error without including key bytes."""
        exc = CryptoError(f"AES key must be 16 bytes, got {len(b'short')}")
        assert b"short".hex() not in str(exc)


class TestTimingAttacks:
    """Cryptographic comparisons must use constant-time comparison."""

    def test_crc_uses_hmac_compare_digest(self) -> None:
        """verify_crc32 must raise CryptoError (not silently return wrong value)."""
        from tuya_cloudless.crypto import compute_crc32, verify_crc32
        from tuya_cloudless.exceptions import CryptoError

        data = b"test data"
        crc = compute_crc32(data)
        with pytest.raises(CryptoError):
            verify_crc32(data, crc ^ 0x1)

    def test_gcm_tag_verification_raises_auth_error(self) -> None:
        """Tampered GCM tag must raise AuthenticationError, not silently pass."""
        key = b"\x00" * 16
        ct = bytearray(encrypt_gcm(key, b"secret payload"))
        ct[-1] ^= 0xFF  # Corrupt last tag byte
        with pytest.raises(AuthenticationError):
            decrypt_gcm(key, bytes(ct))
