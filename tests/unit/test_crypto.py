"""Unit tests for crypto module."""

from __future__ import annotations

from lib.tuya_cloudless.crypto import generate_session_key, md5_hash


def test_md5_hash_returns_bytes() -> None:
    """MD5 hash should return 16 bytes."""
    result = md5_hash(b"test")
    assert isinstance(result, bytes)
    assert len(result) == 16


def test_md5_hash_deterministic() -> None:
    """MD5 hash should be deterministic."""
    assert md5_hash(b"hello") == md5_hash(b"hello")


def test_md5_hash_different_inputs() -> None:
    """Different inputs should produce different hashes."""
    assert md5_hash(b"hello") != md5_hash(b"world")


def test_generate_session_key() -> None:
    """Session key should be generated from local key."""
    key = generate_session_key("test_local_key")
    assert isinstance(key, bytes)
    assert len(key) == 16


def test_generate_session_key_deterministic() -> None:
    """Same local key should produce same session key."""
    key1 = generate_session_key("my_key")
    key2 = generate_session_key("my_key")
    assert key1 == key2
