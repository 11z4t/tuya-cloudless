"""pytest configuration and fixtures.

This file mocks the Crypto module to allow tests to run without pycryptodome installed.
In production, pycryptodome is required and should be installed via pip.
"""

import sys
from unittest.mock import MagicMock, Mock


# Mock Crypto module if not available (allows pytest to run without venv)
# Reference: PLAT-545 R1 — tests must run without venv
try:
    from Crypto.Cipher import AES  # noqa: F401
except ImportError:
    # Create mock Crypto module structure
    mock_crypto = MagicMock()
    mock_crypto_cipher = MagicMock()

    # Mock AES with ECB and GCM modes
    mock_aes = Mock()
    mock_aes.MODE_ECB = 1
    mock_aes.MODE_GCM = 6
    mock_aes.block_size = 16

    # Mock AES.new() to return a cipher object
    def mock_aes_new(key, mode, **kwargs):
        """Mock AES.new()."""
        cipher = Mock()
        cipher.encrypt = Mock(return_value=b"encrypted_data")
        cipher.decrypt = Mock(return_value=b"decrypted_data")
        cipher.encrypt_and_digest = Mock(return_value=(b"ciphertext", b"tag" * 8))
        cipher.decrypt_and_verify = Mock(return_value=b"plaintext")
        return cipher

    mock_aes.new = mock_aes_new

    mock_crypto_cipher.AES = mock_aes
    mock_crypto.Cipher = mock_crypto_cipher

    sys.modules["Crypto"] = mock_crypto
    sys.modules["Crypto.Cipher"] = mock_crypto_cipher
