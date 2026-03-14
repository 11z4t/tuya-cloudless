"""Unit tests for tuya_cloudless.crypto — AES-ECB, AES-GCM, ECDH."""

from __future__ import annotations

import pytest
from tuya_cloudless.crypto import (
    compute_crc32,
    decrypt_ecb,
    decrypt_gcm,
    decrypt_payload,
    derive_ecb_key,
    derive_session_key,
    encrypt_ecb,
    encrypt_gcm,
    encrypt_payload,
    generate_ecdh_keypair,
    verify_crc32,
)
from tuya_cloudless.exceptions import AuthenticationError, CryptoError, KeyDerivationError


# ── Key derivation ────────────────────────────────────────────────────────────


class TestDeriveEcbKey:
    def test_returns_16_bytes(self) -> None:
        key = derive_ecb_key(b"0123456789abcdef")
        assert len(key) == 16

    def test_deterministic(self) -> None:
        local = b"0123456789abcdef"
        assert derive_ecb_key(local) == derive_ecb_key(local)

    def test_different_keys_produce_different_results(self) -> None:
        k1 = derive_ecb_key(b"0123456789abcdef")
        k2 = derive_ecb_key(b"fedcba9876543210")
        assert k1 != k2

    def test_invalid_key_length_raises(self) -> None:
        with pytest.raises(KeyDerivationError):
            derive_ecb_key(b"short")


# ── AES-ECB (CBC) round-trip ──────────────────────────────────────────────────


class TestAesEcb:
    _KEY = b"\x00" * 16

    def test_roundtrip_short(self) -> None:
        plaintext = b'{"dps":{"1":true}}'
        ct = encrypt_ecb(self._KEY, plaintext)
        assert decrypt_ecb(self._KEY, ct) == plaintext

    def test_roundtrip_long(self) -> None:
        plaintext = b"A" * 100
        ct = encrypt_ecb(self._KEY, plaintext)
        assert decrypt_ecb(self._KEY, ct) == plaintext

    def test_roundtrip_empty(self) -> None:
        plaintext = b""
        ct = encrypt_ecb(self._KEY, plaintext)
        assert decrypt_ecb(self._KEY, ct) == plaintext

    def test_ciphertext_is_padded(self) -> None:
        ct = encrypt_ecb(self._KEY, b"hi")
        assert len(ct) % 16 == 0

    def test_wrong_key_length_raises(self) -> None:
        with pytest.raises(CryptoError):
            encrypt_ecb(b"short", b"data")
        with pytest.raises(CryptoError):
            decrypt_ecb(b"short", b"\x00" * 16)

    def test_ciphertext_not_multiple_of_16_raises(self) -> None:
        with pytest.raises(CryptoError):
            decrypt_ecb(self._KEY, b"\x00" * 17)


# ── AES-GCM round-trip ────────────────────────────────────────────────────────


class TestAesGcm:
    _KEY = b"\x01" * 16

    def test_roundtrip(self) -> None:
        plaintext = b'{"dps":{"1":true}}'
        ct = encrypt_gcm(self._KEY, plaintext)
        assert decrypt_gcm(self._KEY, ct) == plaintext

    def test_roundtrip_empty(self) -> None:
        ct = encrypt_gcm(self._KEY, b"")
        assert decrypt_gcm(self._KEY, ct) == b""

    def test_nonce_is_random(self) -> None:
        ct1 = encrypt_gcm(self._KEY, b"same")
        ct2 = encrypt_gcm(self._KEY, b"same")
        # Different IVs → different ciphertexts
        assert ct1[:12] != ct2[:12]

    def test_tampered_ciphertext_raises(self) -> None:
        ct = bytearray(encrypt_gcm(self._KEY, b"secret"))
        ct[12] ^= 0xFF  # Flip a byte in ciphertext
        with pytest.raises(AuthenticationError):
            decrypt_gcm(self._KEY, bytes(ct))

    def test_wrong_key_raises(self) -> None:
        ct = encrypt_gcm(self._KEY, b"data")
        wrong_key = b"\xFF" * 16
        with pytest.raises(AuthenticationError):
            decrypt_gcm(wrong_key, ct)

    def test_too_short_data_raises(self) -> None:
        with pytest.raises(CryptoError):
            decrypt_gcm(self._KEY, b"\x00" * 5)


# ── ECDH session key ──────────────────────────────────────────────────────────


class TestEcdh:
    def test_keypair_public_is_32_bytes(self) -> None:
        kp = generate_ecdh_keypair()
        assert len(kp.public_key_bytes) == 32

    def test_session_key_is_16_bytes(self) -> None:
        kp_a = generate_ecdh_keypair()
        kp_b = generate_ecdh_keypair()
        local_key = b"0123456789abcdef"
        key = derive_session_key(kp_a.private_key, kp_b.public_key_bytes, local_key)
        assert len(key) == 16

    def test_shared_secret_is_symmetric(self) -> None:
        """Both sides must derive the same session key."""
        kp_a = generate_ecdh_keypair()
        kp_b = generate_ecdh_keypair()
        local_key = b"0123456789abcdef"
        key_a = derive_session_key(kp_a.private_key, kp_b.public_key_bytes, local_key)
        key_b = derive_session_key(kp_b.private_key, kp_a.public_key_bytes, local_key)
        assert key_a == key_b

    def test_invalid_peer_public_key_raises(self) -> None:
        kp = generate_ecdh_keypair()
        with pytest.raises(KeyDerivationError):
            derive_session_key(kp.private_key, b"not_32_bytes", b"0123456789abcdef")

    def test_invalid_local_key_raises(self) -> None:
        kp_a = generate_ecdh_keypair()
        kp_b = generate_ecdh_keypair()
        with pytest.raises(KeyDerivationError):
            derive_session_key(kp_a.private_key, kp_b.public_key_bytes, b"short")


# ── CRC-32 ────────────────────────────────────────────────────────────────────


class TestCrc32:
    def test_known_value(self) -> None:
        # CRC-32 of empty string is 0x00000000
        assert compute_crc32(b"") == 0

    def test_verify_pass(self) -> None:
        data = b"hello world"
        crc = compute_crc32(data)
        assert verify_crc32(data, crc) is True

    def test_verify_fail_raises(self) -> None:
        data = b"hello world"
        crc = compute_crc32(data)
        with pytest.raises(CryptoError):
            verify_crc32(data, crc ^ 0xDEAD)


# ── Unified encrypt_payload / decrypt_payload ─────────────────────────────────


class TestUnifiedPayload:
    _LOCAL_KEY = b"0123456789abcdef"

    @pytest.mark.parametrize("version", ["3.1", "3.2", "3.3"])
    def test_ecb_roundtrip(self, version: str) -> None:
        plaintext = b'{"dps":{"1":true}}'
        ct = encrypt_payload(version, self._LOCAL_KEY, plaintext)
        pt = decrypt_payload(version, self._LOCAL_KEY, ct)
        assert pt == plaintext

    @pytest.mark.parametrize("version", ["3.4", "3.5"])
    def test_gcm_roundtrip(self, version: str) -> None:
        plaintext = b'{"dps":{"1":true}}'
        session_key = b"\xAB" * 16
        ct = encrypt_payload(version, self._LOCAL_KEY, plaintext, session_key=session_key)
        pt = decrypt_payload(version, self._LOCAL_KEY, ct, session_key=session_key)
        assert pt == plaintext

    def test_gcm_without_session_key_raises(self) -> None:
        with pytest.raises(CryptoError):
            encrypt_payload("3.4", self._LOCAL_KEY, b"data")
