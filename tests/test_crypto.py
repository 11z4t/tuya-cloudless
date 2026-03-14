"""Tests for Tuya cryptographic functions."""

import pytest

from lib.tuya_cloudless.crypto import (
    TuyaCrypto,
    decrypt_payload,
    encrypt_payload,
    generate_device_id_hash,
)
from lib.tuya_cloudless.exceptions import TuyaCryptoError


class TestTuyaCrypto:
    """Test TuyaCrypto class."""

    def test_init_valid_key(self) -> None:
        """Test initialization with valid key."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")
        assert crypto.protocol_version == "3.3"

    def test_init_invalid_key_length(self) -> None:
        """Test initialization with invalid key length."""
        with pytest.raises(TuyaCryptoError, match="Local key must be 16 characters"):
            TuyaCrypto("short", "3.3")

        with pytest.raises(TuyaCryptoError, match="Local key must be 16 characters"):
            TuyaCrypto("toolongkey1234567890", "3.3")

    def test_init_invalid_version(self) -> None:
        """Test initialization with invalid protocol version."""
        with pytest.raises(TuyaCryptoError, match="Unsupported protocol version"):
            TuyaCrypto("1234567890abcdef", "2.0")

    def test_encrypt_decrypt_v31_no_encryption(self) -> None:
        """Test v3.1 has no encryption."""
        crypto = TuyaCrypto("1234567890abcdef", "3.1")
        plaintext = b"test data"

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert encrypted == plaintext  # No encryption
        assert decrypted == plaintext

    def test_encrypt_decrypt_v32_no_encryption(self) -> None:
        """Test v3.2 has no encryption."""
        crypto = TuyaCrypto("1234567890abcdef", "3.2")
        plaintext = b"test data"

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert encrypted == plaintext  # No encryption
        assert decrypted == plaintext

    def test_encrypt_decrypt_ecb_roundtrip(self) -> None:
        """Test AES-ECB encryption/decryption roundtrip (v3.3)."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")
        plaintext = b"test data for AES-ECB encryption"

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        # Encrypted should be different
        assert encrypted != plaintext

        # Decrypted should match original
        assert decrypted == plaintext

    def test_encrypt_decrypt_gcm_roundtrip_v34(self) -> None:
        """Test AES-GCM encryption/decryption roundtrip (v3.4)."""
        crypto = TuyaCrypto("1234567890abcdef", "3.4")
        plaintext = b"test data for AES-GCM encryption"

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        # Encrypted should be different
        assert encrypted != plaintext

        # Should include nonce (12) + tag (16)
        assert len(encrypted) >= len(plaintext) + 28

        # Decrypted should match original
        assert decrypted == plaintext

    def test_encrypt_decrypt_gcm_roundtrip_v35(self) -> None:
        """Test AES-GCM encryption/decryption roundtrip (v3.5)."""
        crypto = TuyaCrypto("1234567890abcdef", "3.5")
        plaintext = b"test data for AES-GCM v3.5"

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert encrypted != plaintext
        assert decrypted == plaintext

    def test_ecb_padding_block_aligned(self) -> None:
        """Test ECB encryption with block-aligned data."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")
        # 16 bytes (exactly one block)
        plaintext = b"0123456789abcdef"

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert decrypted == plaintext

    def test_ecb_padding_non_aligned(self) -> None:
        """Test ECB encryption with non-block-aligned data."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")
        # 10 bytes (requires padding)
        plaintext = b"test data!"

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert decrypted == plaintext

    def test_ecb_empty_data(self) -> None:
        """Test ECB encryption with empty data."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")
        plaintext = b""

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert decrypted == plaintext

    def test_gcm_authentication_failure(self) -> None:
        """Test GCM authentication fails on tampered data."""
        crypto = TuyaCrypto("1234567890abcdef", "3.4")
        plaintext = b"test data"

        encrypted = crypto.encrypt(plaintext)

        # Tamper with ciphertext (flip a bit)
        tampered = bytearray(encrypted)
        tampered[-1] ^= 0xFF
        tampered_bytes = bytes(tampered)

        with pytest.raises(TuyaCryptoError, match="GCM decryption or authentication failed"):
            crypto.decrypt(tampered_bytes)

    def test_gcm_ciphertext_too_short(self) -> None:
        """Test GCM decryption fails with too short ciphertext."""
        crypto = TuyaCrypto("1234567890abcdef", "3.4")

        # Less than nonce(12) + tag(16) = 28 bytes
        short_data = b"short"

        with pytest.raises(TuyaCryptoError, match="GCM ciphertext too short"):
            crypto.decrypt(short_data)

    def test_pkcs7_unpad_invalid_padding(self) -> None:
        """Test PKCS7 unpadding with invalid padding."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")

        # Create invalid padded data
        invalid_padded = b"test data" + b"\x00\x00\x00\x00\x00\x00\x00"

        with pytest.raises(TuyaCryptoError, match="Invalid padding"):
            crypto._pkcs7_unpad(invalid_padded)

    def test_pkcs7_unpad_empty_data(self) -> None:
        """Test PKCS7 unpadding with empty data."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")

        with pytest.raises(TuyaCryptoError, match="Cannot unpad empty data"):
            crypto._pkcs7_unpad(b"")

    def test_different_keys_produce_different_ciphertext(self) -> None:
        """Test that different keys produce different ciphertext."""
        plaintext = b"test data"

        crypto1 = TuyaCrypto("1234567890abcdef", "3.3")
        crypto2 = TuyaCrypto("fedcba0987654321", "3.3")

        encrypted1 = crypto1.encrypt(plaintext)
        encrypted2 = crypto2.encrypt(plaintext)

        assert encrypted1 != encrypted2

    def test_generate_device_id_hash(self) -> None:
        """Test device ID hash generation."""
        device_id = "test_device_123"
        hash1 = generate_device_id_hash(device_id)

        # Should be MD5 hex (32 characters)
        assert len(hash1) == 32
        assert all(c in "0123456789abcdef" for c in hash1)

        # Same input produces same hash
        hash2 = generate_device_id_hash(device_id)
        assert hash1 == hash2

        # Different input produces different hash
        hash3 = generate_device_id_hash("different_device")
        assert hash1 != hash3

    def test_encrypt_payload_convenience(self) -> None:
        """Test encrypt_payload convenience function."""
        plaintext = b"test data"
        local_key = "1234567890abcdef"

        encrypted = encrypt_payload(plaintext, local_key, "3.3")
        assert isinstance(encrypted, bytes)
        assert encrypted != plaintext

    def test_decrypt_payload_convenience(self) -> None:
        """Test decrypt_payload convenience function."""
        plaintext = b"test data"
        local_key = "1234567890abcdef"

        encrypted = encrypt_payload(plaintext, local_key, "3.3")
        decrypted = decrypt_payload(encrypted, local_key, "3.3")

        assert decrypted == plaintext

    def test_large_payload_ecb(self) -> None:
        """Test ECB encryption with large payload."""
        crypto = TuyaCrypto("1234567890abcdef", "3.3")
        plaintext = b"x" * 10000

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert decrypted == plaintext

    def test_large_payload_gcm(self) -> None:
        """Test GCM encryption with large payload."""
        crypto = TuyaCrypto("1234567890abcdef", "3.4")
        plaintext = b"y" * 10000

        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)

        assert decrypted == plaintext

    def test_gcm_nonce_uniqueness(self) -> None:
        """Test that GCM produces different nonces for same plaintext."""
        crypto = TuyaCrypto("1234567890abcdef", "3.4")
        plaintext = b"test data"

        encrypted1 = crypto.encrypt(plaintext)
        encrypted2 = crypto.encrypt(plaintext)

        # Nonce is first 12 bytes
        nonce1 = encrypted1[:12]
        nonce2 = encrypted2[:12]

        # Nonces should be different (random)
        assert nonce1 != nonce2

        # Both should decrypt correctly
        assert crypto.decrypt(encrypted1) == plaintext
        assert crypto.decrypt(encrypted2) == plaintext
