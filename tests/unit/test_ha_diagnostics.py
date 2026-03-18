"""Tests for custom_components.tuya_cloudless.diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from custom_components.tuya_cloudless.coordinator import DeviceState

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(
    dps: dict | None = None,
    available: bool = True,
    last_seen: datetime | None = None,
    reconnect_count: int = 0,
    last_error: str | None = None,
    session_key: bytes | None = None,
) -> MagicMock:
    coord = MagicMock()
    coord._gw_id = "gw001"
    coord.gw_id = "gw001"
    coord.device_name = "gw001"
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord._writer = MagicMock() if available else None
    coord._sequence = 42
    coord._session_key = session_key
    coord._consecutive_decode_errors = 0
    # Public connection properties used by diagnostics (PLAT-720)
    coord.tcp_connected = available
    coord.sequence_counter = 42
    coord.session_key_active = session_key is not None
    coord.consecutive_decode_errors = 0
    coord.state = DeviceState(
        available=available,
        dps=dps or {},
        last_seen=last_seen,
        reconnect_count=reconnect_count,
        last_error=last_error,
    )
    return coord


def _make_entry(
    gw_id: str = "gw001",
    ip: str = "192.168.1.42",
    version: str = "3.3",
    profile: str = "Generic Switch",
) -> MagicMock:
    from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData

    coord = _make_coordinator()
    entry = MagicMock()
    entry.data = {
        "gw_id": gw_id,
        "ip_address": ip,
        "protocol_version": version,
        "profile": profile,
    }
    entry.runtime_data = TuyaCloudlessRuntimeData(
        coordinator=coord,
        entity_specs=[],
        profile_name=profile,
    )
    return entry


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_returns_config_section(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "config" in result
        # gw_id and ip_address are partially redacted (PLAT-772)
        assert result["config"]["gw_id"].startswith("gw00")
        assert "**REDACTED**" in result["config"]["gw_id"]
        assert result["config"]["ip_address"].startswith("192.168.1.")
        assert result["config"]["ip_address"].endswith(".**")
        assert result["config"]["protocol_version"] == "3.3"
        assert result["config"]["profile"] == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_state_section(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "state" in result
        assert result["state"]["available"] is True
        assert isinstance(result["state"]["dps"], dict)
        assert result["state"]["reconnect_count"] == 0

    @pytest.mark.asyncio
    async def test_returns_connection_section(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "connection" in result
        assert result["connection"]["tcp_connected"] is True
        assert result["connection"]["sequence_counter"] == 42
        assert result["connection"]["session_key_active"] is False
        assert result["connection"]["consecutive_decode_errors"] == 0

    @pytest.mark.asyncio
    async def test_last_seen_formatted(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        ts = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        entry = _make_entry()
        entry.runtime_data.coordinator.state.last_seen = ts
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["state"]["last_seen"] == ts.isoformat()

    @pytest.mark.asyncio
    async def test_last_seen_none(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        entry.runtime_data.coordinator.state.last_seen = None
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["state"]["last_seen"] is None

    @pytest.mark.asyncio
    async def test_no_secrets_in_output(self) -> None:
        """Diagnostics must not include local_key or session key material."""
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        output_str = str(result)
        assert "local_key" not in output_str
        assert "session_key" not in result["config"]


class TestSanitizeDps:
    """Tests for the _sanitize_dps helper (HA-001 / PLAT-841)."""

    def test_short_string_passes_through(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        result = _sanitize_dps({"1": "on"})
        assert result["1"] == "on"

    def test_long_string_redacted(self) -> None:
        """Strings longer than 64 chars must become [REDACTED-LONG]."""
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        long_val = "x" * 65
        result = _sanitize_dps({"2": long_val})
        assert result["2"] == "[REDACTED-LONG]"

    def test_exactly_64_chars_passes_through(self) -> None:
        """Strings of exactly 64 chars should pass through unchanged."""
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        boundary_val = "a" * 64
        result = _sanitize_dps({"3": boundary_val})
        assert result["3"] == boundary_val

    def test_bytes_redacted_with_length(self) -> None:
        """Bytes values must become [REDACTED-BYTES:<length>]."""
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        result = _sanitize_dps({"4": b"\x00\x01\x02"})
        assert result["4"] == "[REDACTED-BYTES:3]"

    def test_bytes_empty_redacted(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        result = _sanitize_dps({"5": b""})
        assert result["5"] == "[REDACTED-BYTES:0]"

    def test_int_passes_through(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        result = _sanitize_dps({"6": 42})
        assert result["6"] == 42

    def test_bool_passes_through(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        result = _sanitize_dps({"7": True})
        assert result["7"] is True

    def test_float_passes_through(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        result = _sanitize_dps({"8": 3.14})
        assert result["8"] == 3.14

    def test_empty_dict(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        assert _sanitize_dps({}) == {}

    def test_all_keys_preserved(self) -> None:
        """All keys must be present in output regardless of value type."""
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        dps = {"1": True, "2": 100, "3": "short", "4": b"bytes", "5": "x" * 65}
        result = _sanitize_dps(dps)
        assert set(result.keys()) == {"1", "2", "3", "4", "5"}

    @pytest.mark.asyncio
    async def test_dps_sanitized_in_diagnostics_output(self) -> None:
        """Long strings and bytes in DPS must be redacted in full diagnostics output."""
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        entry.runtime_data.coordinator.state.dps = {
            "1": True,
            "2": "x" * 65,
            "3": b"\xde\xad\xbe\xef",
            "4": 999,
        }
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        dps = result["state"]["dps"]
        assert dps["1"] is True
        assert dps["2"] == "[REDACTED-LONG]"
        assert dps["3"] == "[REDACTED-BYTES:4]"
        assert dps["4"] == 999
