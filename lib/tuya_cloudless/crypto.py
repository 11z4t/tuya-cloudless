"""Cryptographic operations for Tuya LAN protocol v3.1-3.5.

SECURITY PRINCIPLES:
  - Key material is NEVER logged, printed, or included in exception messages.
  - All MAC/tag verification uses constant-time comparison (hmac.compare_digest).
  - AES-ECB is used ONLY for single-block MD5-key derivation (v3.1-3.3).
    Multi-block payloads use CBC mode — ECB weakness does not apply here.
  - AES-GCM (v3.4/3.5) provides authenticated encryption with ECDH-derived keys.

Protocol version mapping:
  v3.1 — plaintext DPS + AES-ECB for some commands (key = MD5(localkey))
  v3.2 — AES-ECB full payload (key = MD5(localkey))
  v3.3 — AES-ECB + 12-byte version header in payload
  v3.4 — ECDH session key + AES-GCM-128 (12-byte IV, 12-byte tag)
  v3.5 — Same as v3.4, extended DPS encoding
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from typing import NamedTuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tuya_cloudless.const import (
    GCM_IV_SIZE,
    GCM_TAG_SIZE,
    MD5_KEY_PREFIX,
    SUPPORTED_VERSIONS,
    V33_PAYLOAD_HEADER,
    VERSIONS_GCM,
    VERSIONS_WITH_PAYLOAD_HEADER,
)
from tuya_cloudless.exceptions import AuthenticationError, CryptoError, KeyDerivationError

# ── Internal constants ────────────────────────────────────────────────────────

_AES_BLOCK = 16
_MD5_KEY_BYTES = 16  # Only the first 16 bytes of the MD5 digest are used


# ── Key derivation ────────────────────────────────────────────────────────────


def derive_ecb_key(local_key: bytes) -> bytes:
    """Derive the AES-ECB encryption key for protocol v3.1-3.3.

    The key is the first 16 bytes of MD5(MD5_KEY_PREFIX + local_key).
    ``local_key`` is the per-device 16-byte secret obtained during provisioning.

    Args:
        local_key: 16-byte device local key.

    Returns:
        16-byte AES key for ECB operations.

    Raises:
        KeyDerivationError: If ``local_key`` is not 16 bytes.
    """
    if len(local_key) != _AES_BLOCK:
        msg = f"local_key must be {_AES_BLOCK} bytes, got {len(local_key)}"
        raise KeyDerivationError(msg)
    digest = hashlib.md5(MD5_KEY_PREFIX + local_key).digest()  # nosec B324
    return digest[:_MD5_KEY_BYTES]


def _pad_pkcs7(data: bytes) -> bytes:
    """PKCS#7 pad data to AES block boundary."""
    pad_len = _AES_BLOCK - (len(data) % _AES_BLOCK)
    return data + bytes([pad_len] * pad_len)


def _unpad_pkcs7(data: bytes) -> bytes:
    """Remove PKCS#7 padding.

    Raises:
        CryptoError: If padding is invalid.
    """
    if not data:
        raise CryptoError("Cannot unpad empty data")
    pad_len = data[-1]
    if pad_len == 0 or pad_len > _AES_BLOCK:
        raise CryptoError(f"Invalid PKCS7 padding byte: {pad_len}")
    if any(b != pad_len for b in data[-pad_len:]):
        raise CryptoError("PKCS7 padding bytes inconsistent")
    return data[:-pad_len]


# ── AES-ECB (v3.1-3.3) ───────────────────────────────────────────────────────


def encrypt_ecb(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt ``plaintext`` with AES-128-CBC (Tuya uses CBC for multi-block payloads).

    Despite being named ECB-variants in some community docs, Tuya's actual
    v3.1-3.3 LAN payload encryption uses AES-128-CBC with a zero IV.
    Key is derived via :func:`derive_ecb_key`.

    Args:
        key: 16-byte AES key (from :func:`derive_ecb_key`).
        plaintext: Arbitrary-length plaintext (will be PKCS7-padded).

    Returns:
        Ciphertext bytes.

    Raises:
        CryptoError: If key length is wrong.
    """
    if len(key) != _AES_BLOCK:
        raise CryptoError(f"AES key must be {_AES_BLOCK} bytes, got {len(key)}")
    padded = _pad_pkcs7(plaintext)
    iv = b"\x00" * _AES_BLOCK
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_ecb(key: bytes, ciphertext: bytes) -> bytes:
    """Decrypt ``ciphertext`` with AES-128-CBC (zero IV) and remove PKCS7 padding.

    Args:
        key: 16-byte AES key (from :func:`derive_ecb_key`).
        ciphertext: Encrypted bytes (must be a multiple of 16).

    Returns:
        Decrypted plaintext bytes.

    Raises:
        CryptoError: If key length is wrong or padding is invalid.
    """
    if len(key) != _AES_BLOCK:
        raise CryptoError(f"AES key must be {_AES_BLOCK} bytes, got {len(key)}")
    if len(ciphertext) % _AES_BLOCK != 0:
        raise CryptoError(
            f"Ciphertext length {len(ciphertext)} is not a multiple of {_AES_BLOCK}"
        )
    iv = b"\x00" * _AES_BLOCK
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return _unpad_pkcs7(padded)


# ── Payload header handling (v3.3) ────────────────────────────────────────────


def add_v33_header(plaintext: bytes) -> bytes:
    """Prepend the 12-byte version header used by v3.3 payloads.

    Args:
        plaintext: Raw (unencrypted) payload bytes.

    Returns:
        Bytes with header prepended.
    """
    return V33_PAYLOAD_HEADER + plaintext


def strip_v33_header(data: bytes) -> bytes:
    """Strip the 12-byte v3.3 version header from a decrypted payload.

    Args:
        data: Decrypted payload bytes (may or may not have header).

    Returns:
        Payload bytes with header removed if present.
    """
    if data[:3] in (b"3.3", b"3.4", b"3.5"):
        return data[_AES_BLOCK - 4:]  # 12-byte header
    return data


# ── AES-GCM (v3.4/3.5) ───────────────────────────────────────────────────────


def encrypt_gcm(key: bytes, plaintext: bytes, *, extra_nonce: bytes = b"") -> bytes:
    """Encrypt ``plaintext`` with AES-128-GCM.

    The nonce is: 12 random bytes XOR'd with ``extra_nonce`` (if provided,
    used for session sequence counters in v3.5).

    Wire format: [12-byte IV][ciphertext][16-byte GCM tag]

    Args:
        key: 16-byte AES key (from ECDH key agreement).
        plaintext: Data to encrypt.
        extra_nonce: Optional 12-byte value XOR'd into the IV.

    Returns:
        IV + ciphertext + 16-byte GCM-tag bytes.

    Raises:
        CryptoError: If key or extra_nonce have wrong lengths.
    """
    if len(key) != _AES_BLOCK:
        raise CryptoError(f"AES-GCM key must be {_AES_BLOCK} bytes, got {len(key)}")
    iv = os.urandom(GCM_IV_SIZE)
    if extra_nonce:
        if len(extra_nonce) != GCM_IV_SIZE:
            raise CryptoError(
                f"extra_nonce must be {GCM_IV_SIZE} bytes, got {len(extra_nonce)}"
            )
        iv = bytes(a ^ b for a, b in zip(iv, extra_nonce, strict=True))
    aesgcm = AESGCM(key)
    # AESGCM.encrypt returns ciphertext + 16-byte tag (standard GCM 128-bit tag)
    ct_and_tag = aesgcm.encrypt(iv, plaintext, None)
    return iv + ct_and_tag


def decrypt_gcm(key: bytes, data: bytes) -> bytes:
    """Decrypt AES-128-GCM data.

    Expected wire format: [12-byte IV][ciphertext][16-byte GCM tag]

    The AESGCM library expects the data as ciphertext+tag concatenated.
    Since ``data = IV + ciphertext + tag``, we pass ``data[GCM_IV_SIZE:]``
    directly to the library.

    Args:
        key: 16-byte AES key.
        data: IV + ciphertext + 16-byte GCM tag bytes.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        AuthenticationError: If GCM tag verification fails.
        CryptoError: If ``data`` is too short or key is wrong.
    """
    if len(key) != _AES_BLOCK:
        raise CryptoError(f"AES-GCM key must be {_AES_BLOCK} bytes, got {len(key)}")
    min_len = GCM_IV_SIZE + GCM_TAG_SIZE  # 12 + 16 = 28 bytes minimum
    if len(data) < min_len:
        raise CryptoError(
            f"GCM data too short: {len(data)} bytes (minimum {min_len})"
        )
    iv = data[:GCM_IV_SIZE]
    # Remaining bytes = ciphertext + 16-byte tag (AESGCM.decrypt splits them internally)
    ct_plus_tag = data[GCM_IV_SIZE:]
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(iv, ct_plus_tag, None)
    except (InvalidTag, ValueError) as exc:
        raise AuthenticationError(
            "AES-GCM authentication tag mismatch — payload may be tampered"
        ) from exc


# ── ECDH session key exchange (v3.4/3.5) ─────────────────────────────────────


class ECDHKeyPair(NamedTuple):
    """X25519 key pair for Tuya v3.4/3.5 session negotiation."""

    private_key: X25519PrivateKey
    public_key_bytes: bytes  # 32 raw bytes, sent to device


def generate_ecdh_keypair() -> ECDHKeyPair:
    """Generate a fresh X25519 ephemeral key pair.

    Returns:
        :class:`ECDHKeyPair` with the private key and 32-byte public key.
    """
    private = X25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return ECDHKeyPair(private_key=private, public_key_bytes=public_bytes)


def derive_session_key(
    private_key: X25519PrivateKey,
    peer_public_bytes: bytes,
    local_key: bytes,
) -> bytes:
    """Derive the 16-byte AES session key from ECDH shared secret.

    Tuya v3.4/3.5 key derivation:
      shared = X25519(our_private, peer_public)
      session_key = HMAC-SHA256(local_key, shared)[:16]

    Args:
        private_key: Our X25519 private key from :func:`generate_ecdh_keypair`.
        peer_public_bytes: 32-byte raw public key received from device.
        local_key: 16-byte device local key (from provisioning).

    Returns:
        16-byte session key.

    Raises:
        KeyDerivationError: If inputs have wrong lengths or ECDH fails.
    """
    if len(peer_public_bytes) != 32:
        raise KeyDerivationError(
            f"Peer public key must be 32 bytes, got {len(peer_public_bytes)}"
        )
    if len(local_key) != _AES_BLOCK:
        raise KeyDerivationError(
            f"local_key must be {_AES_BLOCK} bytes, got {len(local_key)}"
        )
    try:
        peer_public = X25519PublicKey.from_public_bytes(peer_public_bytes)
        shared_secret = private_key.exchange(peer_public)
    except (ValueError, TypeError) as exc:
        raise KeyDerivationError("X25519 ECDH exchange failed") from exc

    mac = hmac.new(local_key, shared_secret, hashlib.sha256).digest()
    return mac[:_MD5_KEY_BYTES]


# ── CRC-32 checksum (v3.1-3.3 frame integrity) ───────────────────────────────


def compute_crc32(data: bytes) -> int:
    """Compute CRC-32 checksum for Tuya LAN frame integrity.

    Used in protocol versions 3.1-3.3. Versions 3.4/3.5 use GCM tags instead.

    Args:
        data: Bytes to checksum (typically: prefix + seq + cmd + length + payload).

    Returns:
        Unsigned 32-bit CRC value.
    """
    import binascii
    return binascii.crc32(data) & 0xFFFFFFFF


def verify_crc32(data: bytes, expected: int) -> bool:
    """Verify CRC-32 checksum using constant-time comparison.

    Args:
        data: Bytes that were checksummed.
        expected: 4-byte CRC from packet.

    Returns:
        True if CRC matches.

    Raises:
        CryptoError: If CRC does not match.
    """
    computed = compute_crc32(data)
    # Pack both as 4-byte big-endian for constant-time comparison
    expected_bytes = struct.pack(">I", expected)
    computed_bytes = struct.pack(">I", computed)
    if not hmac.compare_digest(expected_bytes, computed_bytes):
        raise CryptoError("CRC-32 mismatch — frame may be corrupted")
    return True


# ── Unified encrypt/decrypt by version ───────────────────────────────────────


def encrypt_payload(
    version: str,
    local_key: bytes,
    plaintext: bytes,
    session_key: bytes | None = None,
) -> bytes:
    """Encrypt a DPS payload according to the protocol version.

    Args:
        version: Protocol version string (e.g. "3.3").
        local_key: 16-byte device local key.
        plaintext: Raw JSON payload bytes.
        session_key: 16-byte ECDH session key (required for v3.4/3.5).

    Returns:
        Encrypted payload bytes (including version header for v3.3+).

    Raises:
        CryptoError: On key length or encryption errors.
        ValueError: If version is unsupported.
    """
    if version not in SUPPORTED_VERSIONS:
        raise CryptoError(f"Unsupported protocol version: {version}")
    if version in VERSIONS_GCM:
        if session_key is None:
            raise CryptoError("session_key required for v3.4/3.5 encryption")
        return encrypt_gcm(session_key, plaintext)

    key = derive_ecb_key(local_key)
    if version in VERSIONS_WITH_PAYLOAD_HEADER:
        plaintext = add_v33_header(plaintext)
    return encrypt_ecb(key, plaintext)


def decrypt_payload(
    version: str,
    local_key: bytes,
    ciphertext: bytes,
    session_key: bytes | None = None,
) -> bytes:
    """Decrypt a DPS payload according to the protocol version.

    Args:
        version: Protocol version string.
        local_key: 16-byte device local key.
        ciphertext: Encrypted payload bytes.
        session_key: 16-byte ECDH session key (required for v3.4/3.5).

    Returns:
        Decrypted plaintext bytes (version header stripped for v3.3+).

    Raises:
        CryptoError / AuthenticationError: On decryption or tag failure.
    """
    if version not in SUPPORTED_VERSIONS:
        raise CryptoError(f"Unsupported protocol version: {version}")
    if version in VERSIONS_GCM:
        if session_key is None:
            raise CryptoError("session_key required for v3.4/3.5 decryption")
        return decrypt_gcm(session_key, ciphertext)

    key = derive_ecb_key(local_key)
    plain = decrypt_ecb(key, ciphertext)
    if version in VERSIONS_WITH_PAYLOAD_HEADER:
        plain = strip_v33_header(plain)
    return plain
