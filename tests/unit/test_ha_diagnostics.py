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
