"""Unit tests for the Tuya Cloudless diagnostics module."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest


def _make_entry(gw_id: str = "gw001") -> MagicMock:
    entry = MagicMock()
    entry.data = {
        "gw_id": gw_id,
        "ip_address": "192.168.1.100",
        "protocol_version": "3.3",
        "profile": "Smart Plug",
    }

    coord = MagicMock()
    coord._writer = MagicMock()
    coord._sequence = 42
    coord._session_key = None
    coord._consecutive_decode_errors = 0
    # Public connection properties (PLAT-720)
    coord.tcp_connected = True
    coord.sequence_counter = 42
    coord.session_key_active = False
    coord.consecutive_decode_errors = 0

    state = MagicMock()
    state.available = True
    state.last_seen = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    state.reconnect_count = 3
    state.last_error = "None"
    state.dps = {"1": True, "19": 1500}
    coord.state = state

    runtime = MagicMock()
    runtime.coordinator = coord
    runtime.profile_name = "Smart Plug"
    entry.runtime_data = runtime
    return entry


class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_returns_config_section(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "config" in result
        config = result["config"]
        assert config["gw_id"] == "gw001"
        assert config["ip_address"] == "192.168.1.100"
        assert config["protocol_version"] == "3.3"
        assert config["profile"] == "Smart Plug"

    @pytest.mark.asyncio
    async def test_returns_state_section(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        state = result["state"]
        assert state["available"] is True
        assert "2024-01-15" in state["last_seen"]
        assert state["reconnect_count"] == 3
        assert state["dps"] == {"1": True, "19": 1500}

    @pytest.mark.asyncio
    async def test_returns_connection_section(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        conn = result["connection"]
        assert conn["tcp_connected"] is True
        assert conn["sequence_counter"] == 42
        assert conn["session_key_active"] is False
        assert conn["consecutive_decode_errors"] == 0

    @pytest.mark.asyncio
    async def test_tcp_not_connected_when_writer_none(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        entry.runtime_data.coordinator._writer = None
        entry.runtime_data.coordinator.tcp_connected = False  # update public property too
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["connection"]["tcp_connected"] is False

    @pytest.mark.asyncio
    async def test_session_key_active_when_set(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        entry.runtime_data.coordinator._session_key = b"\x00" * 32
        entry.runtime_data.coordinator.session_key_active = True  # update public property too
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["connection"]["session_key_active"] is True

    @pytest.mark.asyncio
    async def test_last_seen_none_when_not_connected(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        entry.runtime_data.coordinator.state.last_seen = None
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["state"]["last_seen"] is None
