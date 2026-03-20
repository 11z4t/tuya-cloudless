"""Unit tests for the Tuya Cloudless diagnostics module."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.redact import REDACTED


def _make_entry(gw_id: str = "gw001") -> MagicMock:
    entry = MagicMock()
    entry.data = {
        "gw_id": gw_id,
        "local_key": "a1b2c3d4e5f60011223344556677889900aabbcc",  # 40-char key — must never appear
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


class TestDiagnosticsRedaction:
    """AC1-AC3: Verify async_redact_data is used and secrets are never exposed."""

    @pytest.mark.asyncio
    async def test_local_key_never_in_output(self) -> None:
        """AC2: local_key must never appear anywhere in diagnostics output."""
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        result_str = str(result)
        assert entry.data["local_key"] not in result_str
        assert "a1b2c3d4e5f60011223344556677889900aabbcc" not in result_str

    @pytest.mark.asyncio
    async def test_local_key_replaced_with_redacted(self) -> None:
        """AC1: async_redact_data replaces local_key with REDACTED marker."""
        from homeassistant.helpers.redact import async_redact_data

        from custom_components.tuya_cloudless.diagnostics import (
            _CONFIG_REDACT,
        )

        raw = {
            "gw_id": "abcdef1234",
            "local_key": "secretkey1234567890abcdef12345678",
            "ip_address": "10.0.0.42",
        }
        redacted = async_redact_data(raw, _CONFIG_REDACT)
        assert redacted["local_key"] == REDACTED
        assert "secretkey" not in str(redacted)

    @pytest.mark.asyncio
    async def test_gw_id_partially_redacted(self) -> None:
        """gw_id: first 4 chars preserved, rest replaced with REDACTED."""
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry(gw_id="abcd1234567890")
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        gw = result["config"]["gw_id"]
        # First 4 chars visible
        assert gw.startswith("abcd")
        # Rest is redacted
        assert "1234567890" not in gw
        assert REDACTED in gw

    @pytest.mark.asyncio
    async def test_gw_id_short_fully_redacted(self) -> None:
        """gw_id shorter than 4 chars → fully REDACTED."""
        from custom_components.tuya_cloudless.diagnostics import (
            _partial_gw_id,
        )

        assert _partial_gw_id("abc") == REDACTED

    @pytest.mark.asyncio
    async def test_ip_address_last_octet_redacted(self) -> None:
        """IP address last octet must be replaced with **.

        Shelly redacts ssid; we redact IP last octet for privacy equivalence.
        """
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        ip = result["config"]["ip_address"]
        # Network prefix preserved for debugging
        assert ip.startswith("192.168.1.")
        # Last octet gone
        assert "100" not in ip
        assert ip.endswith(".**")

    @pytest.mark.asyncio
    async def test_ip_malformed_fully_redacted(self) -> None:
        """Malformed IP (no dot) → REDACTED."""
        from custom_components.tuya_cloudless.diagnostics import _partial_ip

        assert _partial_ip("not-an-ip") == REDACTED

    @pytest.mark.asyncio
    async def test_non_sensitive_fields_preserved(self) -> None:
        """protocol_version, profile, dps, connection fields must pass through unchanged."""
        from custom_components.tuya_cloudless.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = _make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["config"]["protocol_version"] == "3.3"
        assert result["config"]["profile"] == "Smart Plug"
        assert result["state"]["dps"] == {"1": True, "19": 1500}
        assert result["connection"]["sequence_counter"] == 42

    @pytest.mark.asyncio
    async def test_uses_async_redact_data(self) -> None:
        """AC1: Verify async_redact_data is imported and used in diagnostics module."""
        import custom_components.tuya_cloudless.diagnostics as diag_mod

        assert hasattr(diag_mod, "_CONFIG_REDACT"), (
            "diagnostics must define _CONFIG_REDACT mapping for async_redact_data"
        )
        assert "local_key" in diag_mod._CONFIG_REDACT
        assert "gw_id" in diag_mod._CONFIG_REDACT
        assert "ip_address" in diag_mod._CONFIG_REDACT


class TestSanitizeLastError:
    def test_ip_in_last_error_is_redacted(self) -> None:
        """last_error strings containing LAN IPs must have the last octet replaced."""
        from custom_components.tuya_cloudless.diagnostics import _sanitize_last_error

        raw = "Connect call failed ('192.168.1.50', 6668)"
        result = _sanitize_last_error(raw)
        assert result is not None
        assert "192.168.1.**" in result
        assert "50" not in result

    def test_none_last_error_returns_none(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_last_error

        assert _sanitize_last_error(None) is None

    def test_no_ip_passes_through(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_last_error

        msg = "[Errno 111] Connection refused"
        assert _sanitize_last_error(msg) == msg

    def test_multiple_ips_all_redacted(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_last_error

        raw = "Tried 10.0.0.1 and 192.168.0.99 without success"
        result = _sanitize_last_error(raw)
        assert result is not None
        # Both IPs must be replaced with last-octet-redacted form
        assert "10.0.0.**" in result
        assert "192.168.0.**" in result
        # Original last octets must not appear as standalone values
        assert "10.0.0.1" not in result
        assert "192.168.0.99" not in result

    def test_ipv6_address_in_error_is_redacted(self) -> None:
        """R46-F4: IPv6 addresses embedded in error strings must be redacted."""
        from custom_components.tuya_cloudless.diagnostics import _sanitize_last_error

        raw = "Connect failed to 2001:db8::1 port 6668"
        result = _sanitize_last_error(raw)
        assert result is not None
        assert "2001:db8::1" not in result
        assert "[IPv6-REDACTED]" in result

    def test_loopback_ipv6_redacted(self) -> None:
        """::1 (IPv6 loopback) must be redacted from error strings."""
        from custom_components.tuya_cloudless.diagnostics import _sanitize_last_error

        raw = "Connection refused (::1, 6668)"
        result = _sanitize_last_error(raw)
        assert result is not None
        assert "[IPv6-REDACTED]" in result


class TestSanitizeDpsNested:
    def test_nested_dict_is_redacted(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        dps = {"1": {"key": "value"}, "2": True}
        result = _sanitize_dps(dps)
        assert result["1"] == "[REDACTED-NESTED]"
        assert result["2"] is True

    def test_nested_list_is_redacted(self) -> None:
        from custom_components.tuya_cloudless.diagnostics import _sanitize_dps

        dps = {"3": [1, 2, 3]}
        result = _sanitize_dps(dps)
        assert result["3"] == "[REDACTED-NESTED]"
