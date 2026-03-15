"""Cryptographic functions for Tuya protocol encryption/decryption."""

import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .exceptions import TuyaCryptoError

# Block size for AES
AES_BLOCK_SIZE = 16

# UDP discovery key (MD5 hash of magic string)
# Verified against tinytuya/core/udp_helper.py line 21 and localtuya/discovery.py line 17
UDP_KEY = hashlib.md5(b"yGAdlopoPVldABfn").digest()


class TuyaCrypto:
    """Handles encryption and decryption for Tuya devices."""

    def __init__(self, local_key: str | bytes, protocol_version: str = "3.3") -> None:
        """Initialize crypto handler.

        Args:
            local_key: Device local key (16 characters string or 16 bytes)
            protocol_version: Protocol version ("3.1", "3.2", "3.3", "3.4", "3.5")

        Raises:
            TuyaCryptoError: If local_key is invalid
        """
        if not local_key or len(local_key) != 16:
            raise TuyaCryptoError(f"Local key must be 16 characters, got {len(local_key)}")

        if protocol_version not in ("3.1", "3.2", "3.3", "3.4", "3.5"):
            raise TuyaCryptoError(f"Unsupported protocol version: {protocol_version}")

        # Support both string and bytes (UDP_KEY is bytes)
        self.local_key = local_key.encode("utf-8") if isinstance(local_key, str) else local_key
        self.protocol_version = protocol_version

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt data according to protocol version.

        Args:
            plaintext: Data to encrypt

        Returns:
            Encrypted data

        Raises:
            TuyaCryptoError: If encryption fails
        """
        if self.protocol_version in ("3.1", "3.2"):
            # No encryption for v3.1 and v3.2
            return plaintext

        if self.protocol_version == "3.3":
            # AES-ECB for v3.3
            return self._encrypt_ecb(plaintext)

        if self.protocol_version in ("3.4", "3.5"):
            # AES-GCM for v3.4 and v3.5
            return self._encrypt_gcm(plaintext)

        raise TuyaCryptoError(f"Unsupported protocol version: {self.protocol_version}")

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt data according to protocol version.

        Args:
            ciphertext: Data to decrypt

        Returns:
            Decrypted data

        Raises:
            TuyaCryptoError: If decryption fails
        """
        if self.protocol_version in ("3.1", "3.2"):
            # No decryption for v3.1 and v3.2
            return ciphertext

        if self.protocol_version == "3.3":
            # AES-ECB for v3.3
            return self._decrypt_ecb(ciphertext)

        if self.protocol_version in ("3.4", "3.5"):
            # AES-GCM for v3.4 and v3.5
            return self._decrypt_gcm(ciphertext)

        raise TuyaCryptoError(f"Unsupported protocol version: {self.protocol_version}")

    def _encrypt_ecb(self, plaintext: bytes) -> bytes:
        """Encrypt using AES-ECB mode (v3.3).

        Args:
            plaintext: Data to encrypt

        Returns:
            Encrypted data with PKCS7 padding

        Raises:
            TuyaCryptoError: If encryption fails
        """
        try:
            # Apply PKCS7 padding
            padded = self._pkcs7_pad(plaintext)

            # Create cipher and encrypt
            cipher = Cipher(algorithms.AES(self.local_key), modes.ECB())
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(padded) + encryptor.finalize()

            return ciphertext

        except (ValueError, TypeError) as e:
            raise TuyaCryptoError(f"ECB encryption failed: {e}") from e

    def _decrypt_ecb(self, ciphertext: bytes) -> bytes:
        """Decrypt using AES-ECB mode (v3.3).

        Args:
            ciphertext: Data to decrypt

        Returns:
            Decrypted data with padding removed

        Raises:
            TuyaCryptoError: If decryption fails
        """
        try:
            # Create cipher and decrypt
            cipher = Cipher(algorithms.AES(self.local_key), modes.ECB())
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()

            # Remove PKCS7 padding
            plaintext = self._pkcs7_unpad(padded)

            return plaintext

        except (ValueError, TypeError) as e:
            raise TuyaCryptoError(f"ECB decryption failed: {e}") from e

    def _encrypt_gcm(self, plaintext: bytes) -> bytes:
        """Encrypt using AES-GCM mode (v3.4, v3.5).

        Args:
            plaintext: Data to encrypt

        Returns:
            Encrypted data: nonce(12) + tag(16) + ciphertext

        Raises:
            TuyaCryptoError: If encryption fails
        """
        try:
            # Generate nonce (12 bytes for GCM)
            nonce = os.urandom(12)

            # Encrypt: AESGCM.encrypt returns ciphertext + tag (tag is last 16 bytes)
            aesgcm = AESGCM(self.local_key)
            ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)

            # Separate ciphertext and tag, return nonce + tag + ciphertext
            ciphertext = ct_with_tag[:-16]
            tag = ct_with_tag[-16:]
            return nonce + tag + ciphertext

        except (ValueError, TypeError) as e:
            raise TuyaCryptoError(f"GCM encryption failed: {e}") from e

    def _decrypt_gcm(self, ciphertext: bytes) -> bytes:
        """Decrypt using AES-GCM mode (v3.4, v3.5).

        Args:
            ciphertext: Data to decrypt (nonce + tag + ciphertext)

        Returns:
            Decrypted data

        Raises:
            TuyaCryptoError: If decryption fails or authentication fails
        """
        if len(ciphertext) < 28:  # 12 (nonce) + 16 (tag)
            raise TuyaCryptoError(f"GCM ciphertext too short: {len(ciphertext)} bytes")

        try:
            # Extract components: nonce(12) + tag(16) + ciphertext
            nonce = ciphertext[:12]
            tag = ciphertext[12:28]
            encrypted_data = ciphertext[28:]

            # AESGCM.decrypt expects ciphertext + tag appended at end
            aesgcm = AESGCM(self.local_key)
            plaintext = aesgcm.decrypt(nonce, encrypted_data + tag, None)

            return plaintext

        except (InvalidTag, ValueError, TypeError) as e:
            raise TuyaCryptoError(f"GCM decryption or authentication failed: {e}") from e

    def _pkcs7_pad(self, data: bytes) -> bytes:
        """Apply PKCS7 padding to data.

        Args:
            data: Data to pad

        Returns:
            Padded data
        """
        padding_length = AES_BLOCK_SIZE - (len(data) % AES_BLOCK_SIZE)
        padding = bytes([padding_length] * padding_length)
        return data + padding

    def _pkcs7_unpad(self, data: bytes) -> bytes:
        """Remove PKCS7 padding from data.

        Args:
            data: Padded data

        Returns:
            Unpadded data

        Raises:
            TuyaCryptoError: If padding is invalid
        """
        if not data:
            raise TuyaCryptoError("Cannot unpad empty data")

        padding_length = data[-1]

        if padding_length < 1 or padding_length > AES_BLOCK_SIZE:
            raise TuyaCryptoError(f"Invalid padding length: {padding_length}")

        # Verify padding bytes
        if data[-padding_length:] != bytes([padding_length] * padding_length):
            raise TuyaCryptoError("Invalid PKCS7 padding")

        return data[:-padding_length]


def generate_device_id_hash(device_id: str) -> str:
    """Generate MD5 hash of device ID for authentication.

    Args:
        device_id: Device ID

    Returns:
        MD5 hash (lowercase hex)
    """
    return hashlib.md5(device_id.encode("utf-8")).hexdigest()


def encrypt_payload(plaintext: bytes, local_key: str | bytes, protocol_version: str = "3.3") -> bytes:
    """Convenience function to encrypt payload.

    Args:
        plaintext: Data to encrypt
        local_key: Device local key (string or bytes)
        protocol_version: Protocol version

    Returns:
        Encrypted data
    """
    crypto = TuyaCrypto(local_key, protocol_version)
    return crypto.encrypt(plaintext)


def decrypt_payload(ciphertext: bytes, local_key: str | bytes, protocol_version: str = "3.3") -> bytes:
    """Convenience function to decrypt payload.

    Args:
        ciphertext: Data to decrypt
        local_key: Device local key (string or bytes)
        protocol_version: Protocol version

    Returns:
        Decrypted data
    """
    crypto = TuyaCrypto(local_key, protocol_version)
    return crypto.decrypt(ciphertext)
