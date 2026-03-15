"""Unit tests for Tuya LAN message payloads and protocol version handling."""

from __future__ import annotations

import json

import pytest
from tuya_cloudless.const import (
    CMD_CONTROL,
    CMD_HEARTBEAT,
    PROTOCOL_31,
    PROTOCOL_32,
    PROTOCOL_33,
    PROTOCOL_34,
    PROTOCOL_35,
    SUPPORTED_VERSIONS,
    VERSIONS_ECB,
    VERSIONS_GCM,
)
from tuya_cloudless.exceptions import UnsupportedVersionError
from tuya_cloudless.protocol import (
    TuyaFrame,
    decode_frame,
    encode_frame,
    encode_status_query,
)

_LOCAL_KEY = b"0123456789abcdef"


# ── Protocol version constants ─────────────────────────────────────────────────


class TestProtocolVersionConstants:
    def test_all_versions_in_supported(self) -> None:
        for ver in (PROTOCOL_31, PROTOCOL_32, PROTOCOL_33, PROTOCOL_34, PROTOCOL_35):
            assert ver in SUPPORTED_VERSIONS

    def test_supported_versions_count(self) -> None:
        assert len(SUPPORTED_VERSIONS) == 5

    def test_ecb_versions_are_subset(self) -> None:
        assert VERSIONS_ECB.issubset(SUPPORTED_VERSIONS)

    def test_gcm_versions_are_subset(self) -> None:
        assert VERSIONS_GCM.issubset(SUPPORTED_VERSIONS)

    def test_ecb_and_gcm_are_disjoint(self) -> None:
        assert VERSIONS_ECB.isdisjoint(VERSIONS_GCM)

    def test_unsupported_version_raises(self) -> None:
        with pytest.raises(UnsupportedVersionError):
            encode_status_query(sequence=1, version="2.0", local_key=_LOCAL_KEY)


# ── Status query encoding ──────────────────────────────────────────────────────


class TestEncodeStatusQuery:
    def test_returns_bytes_v31(self) -> None:
        raw = encode_status_query(sequence=1, version=PROTOCOL_31, local_key=_LOCAL_KEY)
        assert isinstance(raw, bytes)
        assert len(raw) > 16

    def test_returns_bytes_v33(self) -> None:
        raw = encode_status_query(sequence=1, version=PROTOCOL_33, local_key=_LOCAL_KEY)
        assert isinstance(raw, bytes)
        assert len(raw) > 16

    def test_returns_bytes_v34(self) -> None:
        session_key = b"\xab" * 16
        raw = encode_status_query(
            sequence=1, version=PROTOCOL_34, local_key=_LOCAL_KEY, session_key=session_key
        )
        assert isinstance(raw, bytes)
        assert len(raw) > 16


# ── TuyaFrame DPS payload ──────────────────────────────────────────────────────


class TestTuyaFramePayload:
    def test_empty_payload_dps(self) -> None:
        frame = TuyaFrame(sequence=1, command=CMD_HEARTBEAT, version=PROTOCOL_33, payload=b"")
        assert frame.dps == {}

    def test_v31_plaintext_roundtrip(self) -> None:
        dps = {"1": True, "2": 300}
        payload = json.dumps({"dps": dps}, separators=(",", ":")).encode()
        raw = encode_frame(
            CMD_CONTROL, payload, sequence=10, version=PROTOCOL_31, local_key=_LOCAL_KEY
        )
        frame = decode_frame(raw, version=PROTOCOL_31, local_key=_LOCAL_KEY)
        assert frame.dps == {"dps": dps}

    def test_v33_encrypted_roundtrip(self) -> None:
        dps = {"1": False}
        payload = json.dumps({"dps": dps}, separators=(",", ":")).encode()
        raw = encode_frame(
            CMD_CONTROL, payload, sequence=2, version=PROTOCOL_33, local_key=_LOCAL_KEY
        )
        frame = decode_frame(raw, version=PROTOCOL_33, local_key=_LOCAL_KEY)
        assert frame.dps == {"dps": dps}
