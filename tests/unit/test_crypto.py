"""Unit tests for tuya_cloudless.crypto — AES-ECB, AES-GCM, ECDH."""

from __future__ import annotations

import pytest
from tuya_cloudless.crypto import (
    _pad_pkcs7,
    _unpad_pkcs7,
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

    def test_too_long_key_raises(self) -> None:
        with pytest.raises(KeyDerivationError):
            derive_ecb_key(b"0123456789abcdef" * 2)

    def test_result_is_bytes(self) -> None:
        key = derive_ecb_key(b"0123456789abcdef")
        assert isinstance(key, bytes)


# ── PKCS7 padding ────────────────────────────────────────────────────────────


class TestPkcs7Padding:
    def test_pad_adds_full_block_at_boundary(self) -> None:
        """Input exactly 16 bytes must get 16 bytes of padding (PKCS7 rule)."""
        data = b"A" * 16
        padded = _pad_pkcs7(data)
        assert len(padded) == 32
        assert padded[16:] == bytes([16] * 16)

    def test_pad_short_input(self) -> None:
        padded = _pad_pkcs7(b"hi")
        assert len(padded) == 16
        assert padded[-1] == 14

    def test_pad_empty(self) -> None:
        padded = _pad_pkcs7(b"")
        assert len(padded) == 16
        assert padded == bytes([16] * 16)

    def test_unpad_reverses_pad(self) -> None:
        for length in (0, 1, 15, 16, 31, 32):
            data = b"X" * length
            assert _unpad_pkcs7(_pad_pkcs7(data)) == data

    def test_unpad_empty_raises(self) -> None:
        with pytest.raises(CryptoError):
            _unpad_pkcs7(b"")

    def test_unpad_invalid_zero_pad_byte_raises(self) -> None:
        with pytest.raises(CryptoError):
            _unpad_pkcs7(b"\x00" * 16)

    def test_unpad_invalid_too_large_pad_byte_raises(self) -> None:
        with pytest.raises(CryptoError):
            _unpad_pkcs7(b"\x00" * 15 + b"\x11")

    def test_unpad_inconsistent_padding_raises(self) -> None:
        with pytest.raises(CryptoError):
            _unpad_pkcs7(b"\x00" * 14 + b"\x01\x02")


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

    def test_roundtrip_block_boundary(self) -> None:
        """Plaintext exactly 16 bytes: output must be 32 bytes (PKCS7 full-block padding)."""
        plaintext = b"0123456789abcdef"
        ct = encrypt_ecb(self._KEY, plaintext)
        assert len(ct) == 32
        assert decrypt_ecb(self._KEY, ct) == plaintext

    def test_roundtrip_binary_data(self) -> None:
        plaintext = bytes(range(256))
        ct = encrypt_ecb(self._KEY, plaintext)
        assert decrypt_ecb(self._KEY, ct) == plaintext

    def test_roundtrip_large_payload(self) -> None:
        plaintext = b"B" * 10000
        ct = encrypt_ecb(self._KEY, plaintext)
        assert decrypt_ecb(self._KEY, ct) == plaintext

    def test_different_key_produces_different_ciphertext(self) -> None:
        plaintext = b"test data here!!"
        ct1 = encrypt_ecb(self._KEY, plaintext)
        ct2 = encrypt_ecb(b"\x01" * 16, plaintext)
        assert ct1 != ct2

    def test_decrypt_wrong_key_fails(self) -> None:
        """Decrypting with wrong key must either raise CryptoError or return wrong data."""
        plaintext = b'{"dps":{"1":true}}'
        ct = encrypt_ecb(self._KEY, plaintext)
        wrong_key = b"\xff" * 16
        try:
            result = decrypt_ecb(wrong_key, ct)
            # If padding happened to look valid, plaintext must still differ
            assert result != plaintext
        except CryptoError:
            pass  # Expected: invalid PKCS7 padding

    def test_decrypt_empty_ciphertext_raises(self) -> None:
        """Empty ciphertext is 0 bytes which is a valid multiple of 16, but has no padding."""
        with pytest.raises(CryptoError):
            decrypt_ecb(self._KEY, b"")


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
        # Different IVs -> different ciphertexts
        assert ct1[:12] != ct2[:12]

    def test_tampered_ciphertext_raises(self) -> None:
        ct = bytearray(encrypt_gcm(self._KEY, b"secret"))
        ct[12] ^= 0xFF  # Flip a byte in ciphertext
        with pytest.raises(AuthenticationError):
            decrypt_gcm(self._KEY, bytes(ct))

    def test_wrong_key_raises(self) -> None:
        ct = encrypt_gcm(self._KEY, b"data")
        wrong_key = b"\xff" * 16
        with pytest.raises(AuthenticationError):
            decrypt_gcm(wrong_key, ct)

    def test_too_short_data_raises(self) -> None:
        with pytest.raises(CryptoError):
            decrypt_gcm(self._KEY, b"\x00" * 5)

    def test_roundtrip_large_payload(self) -> None:
        plaintext = b"C" * 50000
        ct = encrypt_gcm(self._KEY, plaintext)
        assert decrypt_gcm(self._KEY, ct) == plaintext

    def test_extra_nonce_roundtrip(self) -> None:
        """extra_nonce is XOR'd into IV; decrypt uses IV from wire — should still work."""
        extra = b"\xaa" * 12
        ct = encrypt_gcm(self._KEY, b"nonce-test", extra_nonce=extra)
        assert decrypt_gcm(self._KEY, ct) == b"nonce-test"

    def test_extra_nonce_wrong_length_raises(self) -> None:
        with pytest.raises(CryptoError):
            encrypt_gcm(self._KEY, b"data", extra_nonce=b"\x00" * 5)

    def test_wrong_key_length_encrypt_raises(self) -> None:
        with pytest.raises(CryptoError):
            encrypt_gcm(b"short", b"data")

    def test_nonce_uniqueness_three_calls(self) -> None:
        """Three encryptions of same plaintext produce three different IVs."""
        ivs = {encrypt_gcm(self._KEY, b"x")[:12] for _ in range(3)}
        assert len(ivs) == 3

    def test_tampered_tag_raises(self) -> None:
        ct = bytearray(encrypt_gcm(self._KEY, b"protected"))
        ct[-1] ^= 0xFF  # Flip last byte of GCM tag
        with pytest.raises(AuthenticationError):
            decrypt_gcm(self._KEY, bytes(ct))

    def test_output_format_iv_ct_tag(self) -> None:
        """Output is exactly: 12-byte IV + ciphertext + 16-byte tag."""
        plaintext = b"hello"
        ct = encrypt_gcm(self._KEY, plaintext)
        # len = 12 (IV) + 5 (ct) + 16 (tag) = 33
        assert len(ct) == 12 + len(plaintext) + 16


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

    def test_keypairs_are_unique(self) -> None:
        kp1 = generate_ecdh_keypair()
        kp2 = generate_ecdh_keypair()
        assert kp1.public_key_bytes != kp2.public_key_bytes

    def test_different_local_keys_produce_different_sessions(self) -> None:
        kp_a = generate_ecdh_keypair()
        kp_b = generate_ecdh_keypair()
        s1 = derive_session_key(kp_a.private_key, kp_b.public_key_bytes, b"0123456789abcdef")
        s2 = derive_session_key(kp_a.private_key, kp_b.public_key_bytes, b"fedcba9876543210")
        assert s1 != s2

    def test_empty_local_key_raises(self) -> None:
        kp_a = generate_ecdh_keypair()
        kp_b = generate_ecdh_keypair()
        with pytest.raises(KeyDerivationError):
            derive_session_key(kp_a.private_key, kp_b.public_key_bytes, b"")


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

    def test_known_nonzero_value(self) -> None:
        import binascii

        expected = binascii.crc32(b"hello") & 0xFFFFFFFF
        assert compute_crc32(b"hello") == expected

    def test_different_data_different_crc(self) -> None:
        assert compute_crc32(b"abc") != compute_crc32(b"xyz")

    def test_verify_returns_true(self) -> None:
        data = b"test data"
        crc = compute_crc32(data)
        result = verify_crc32(data, crc)
        assert result is True


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
        session_key = b"\xab" * 16
        ct = encrypt_payload(version, self._LOCAL_KEY, plaintext, session_key=session_key)
        pt = decrypt_payload(version, self._LOCAL_KEY, ct, session_key=session_key)
        assert pt == plaintext

    def test_gcm_without_session_key_raises(self) -> None:
        with pytest.raises(CryptoError):
            encrypt_payload("3.4", self._LOCAL_KEY, b"data")

    def test_gcm_decrypt_without_session_key_raises(self) -> None:
        session_key = b"\xab" * 16
        ct = encrypt_payload("3.4", self._LOCAL_KEY, b"data", session_key=session_key)
        with pytest.raises(CryptoError):
            decrypt_payload("3.4", self._LOCAL_KEY, ct)

    @pytest.mark.parametrize("version", ["3.1", "3.2", "3.3"])
    def test_ecb_roundtrip_large(self, version: str) -> None:
        plaintext = b"D" * 5000
        ct = encrypt_payload(version, self._LOCAL_KEY, plaintext)
        pt = decrypt_payload(version, self._LOCAL_KEY, ct)
        assert pt == plaintext

    @pytest.mark.parametrize("version", ["3.1", "3.2", "3.3"])
    def test_ecb_roundtrip_empty(self, version: str) -> None:
        ct = encrypt_payload(version, self._LOCAL_KEY, b"")
        pt = decrypt_payload(version, self._LOCAL_KEY, ct)
        assert pt == b""

    @pytest.mark.parametrize("version", ["3.4", "3.5"])
    def test_gcm_roundtrip_large(self, version: str) -> None:
        plaintext = b"E" * 5000
        session_key = b"\xcd" * 16
        ct = encrypt_payload(version, self._LOCAL_KEY, plaintext, session_key=session_key)
        pt = decrypt_payload(version, self._LOCAL_KEY, ct, session_key=session_key)
        assert pt == plaintext

    def test_unsupported_version_encrypt_raises(self) -> None:
        with pytest.raises(CryptoError):
            encrypt_payload("2.0", self._LOCAL_KEY, b"data")

    def test_unsupported_version_decrypt_raises(self) -> None:
        ct = encrypt_ecb(derive_ecb_key(self._LOCAL_KEY), b"data")
        with pytest.raises(CryptoError):
            decrypt_payload("9.9", self._LOCAL_KEY, ct)

    def test_v33_header_roundtrip_preserves_data(self) -> None:
        """v3.3 adds a header during encrypt and strips it during decrypt."""
        plaintext = b'{"dps":{"2":false}}'
        ct = encrypt_payload("3.3", self._LOCAL_KEY, plaintext)
        pt = decrypt_payload("3.3", self._LOCAL_KEY, ct)
        assert pt == plaintext
