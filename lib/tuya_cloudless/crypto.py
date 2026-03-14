"""Cryptographic functions for Tuya protocol encryption/decryption."""

import hashlib

from Crypto.Cipher import AES

from .exceptions import TuyaCryptoError

# Block size for AES
AES_BLOCK_SIZE = 16


class TuyaCrypto:
    """Handles encryption and decryption for Tuya devices."""

    def __init__(self, local_key: str, protocol_version: str = "3.3") -> None:
        """Initialize crypto handler.

        Args:
            local_key: Device local key (16 characters)
            protocol_version: Protocol version ("3.1", "3.2", "3.3", "3.4", "3.5")

        Raises:
            TuyaCryptoError: If local_key is invalid
        """
        if not local_key or len(local_key) != 16:
            raise TuyaCryptoError(f"Local key must be 16 characters, got {len(local_key)}")

        if protocol_version not in ("3.1", "3.2", "3.3", "3.4", "3.5"):
            raise TuyaCryptoError(f"Unsupported protocol version: {protocol_version}")

        self.local_key = local_key.encode("utf-8")
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

            # Create cipher
            cipher = AES.new(self.local_key, AES.MODE_ECB)

            # Encrypt
            ciphertext = cipher.encrypt(padded)

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
            # Create cipher
            cipher = AES.new(self.local_key, AES.MODE_ECB)

            # Decrypt
            padded = cipher.decrypt(ciphertext)

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
            import os

            nonce = os.urandom(12)

            # Create cipher
            cipher = AES.new(self.local_key, AES.MODE_GCM, nonce=nonce)

            # Encrypt and generate authentication tag
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)

            # Return nonce + tag + ciphertext
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
            # Extract components
            nonce = ciphertext[:12]
            tag = ciphertext[12:28]
            encrypted_data = ciphertext[28:]

            # Create cipher
            cipher = AES.new(self.local_key, AES.MODE_GCM, nonce=nonce)

            # Decrypt and verify authentication tag
            plaintext = cipher.decrypt_and_verify(encrypted_data, tag)

            return plaintext

        except (ValueError, TypeError) as e:
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


def encrypt_payload(plaintext: bytes, local_key: str, protocol_version: str = "3.3") -> bytes:
    """Convenience function to encrypt payload.

    Args:
        plaintext: Data to encrypt
        local_key: Device local key
        protocol_version: Protocol version

    Returns:
        Encrypted data
    """
    crypto = TuyaCrypto(local_key, protocol_version)
    return crypto.encrypt(plaintext)


def decrypt_payload(ciphertext: bytes, local_key: str, protocol_version: str = "3.3") -> bytes:
    """Convenience function to decrypt payload.

    Args:
        ciphertext: Data to decrypt
        local_key: Device local key
        protocol_version: Protocol version

    Returns:
        Decrypted data
    """
    crypto = TuyaCrypto(local_key, protocol_version)
    return crypto.decrypt(ciphertext)
