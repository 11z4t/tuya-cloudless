"""Tuya Local protocol cryptography.

All crypto operations for Tuya Local protocol communication.
"""

from __future__ import annotations

import hashlib
import logging

_LOGGER = logging.getLogger(__name__)


def md5_hash(data: bytes) -> bytes:
    """Compute MD5 hash (used by Tuya protocol, not for security)."""
    return hashlib.md5(data).digest()


def generate_session_key(local_key: str) -> bytes:
    """Generate session key from local key."""
    return md5_hash(local_key.encode("utf-8"))
