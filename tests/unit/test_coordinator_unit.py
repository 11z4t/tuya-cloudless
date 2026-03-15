"""Unit tests for the TuyaCloudlessCoordinator (non-async paths).

Tests cover error handling in send_dps, state management, and helper methods.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_coord() -> object:
    """Create a coordinator without starting the connection loop."""
    from custom_components.tuya_cloudless.coordinator import TuyaCloudlessCoordinator

    hass = MagicMock()
    hass.loop = asyncio.get_event_loop()

    coord = TuyaCloudlessCoordinator.__new__(TuyaCloudlessCoordinator)
    coord._hass = hass
    coord._entry_id = "test"
    coord._gw_id = "gw001"
    coord._ip_address = "127.0.0.1"
    coord._local_key = b"0123456789abcdef"
    coord._version = "3.3"
    coord._port = 6668
    coord._heartbeat_interval = 20
    coord._command_timeout = 5.0
    coord._reconnect_max_delay = 300
    coord._writer = None
    coord._session_key = None
    coord._sequence = 0
    coord._listeners: list = []
    coord._connection_task = None
    coord._consecutive_decode_errors = 0

    from custom_components.tuya_cloudless.coordinator import DeviceState

    coord.state = DeviceState()
    return coord


class TestCoordinatorState:
    def test_initial_state_unavailable(self) -> None:
        from custom_components.tuya_cloudless.coordinator import DeviceState

        state = DeviceState()
        assert state.available is False
        assert state.dps == {}
        assert state.last_seen is None
        assert state.reconnect_count == 0

    def test_state_update_dps(self) -> None:
        from custom_components.tuya_cloudless.coordinator import DeviceState

        state = DeviceState()
        state.dps.update({"1": True})
        assert state.dps["1"] is True


class TestCoordinatorSendDps:
    @pytest.mark.asyncio
    async def test_send_dps_raises_when_unavailable(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coord()
        coord.state.available = False  # type: ignore[union-attr]

        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_send_dps_raises_when_writer_none(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coord()
        coord.state.available = True  # type: ignore[union-attr]
        coord._writer = None  # type: ignore[union-attr]

        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_send_dps_timeout_raises_error(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coord()
        coord.state.available = True  # type: ignore[union-attr]
        coord._writer = MagicMock()  # type: ignore[union-attr]

        with (
            patch.object(
                coord.__class__,
                "_do_send_dps",
                side_effect=asyncio.TimeoutError,
            ),
            pytest.raises(HomeAssistantError),
        ):
            await coord.async_send_dps({"1": True})  # type: ignore[union-attr]


class TestCoordinatorSequence:
    def test_next_sequence_increments(self) -> None:
        coord = _make_coord()
        first = coord._next_sequence()  # type: ignore[union-attr]
        second = coord._next_sequence()  # type: ignore[union-attr]
        assert second == first + 1

    def test_sequence_wraps_at_max(self) -> None:
        coord = _make_coord()
        coord._sequence = 0xFFFFFFFF  # type: ignore[union-attr]
        next_val = coord._next_sequence()  # type: ignore[union-attr]
        # Should wrap around (max 32-bit)
        assert next_val >= 0


class TestCoordinatorAttributes:
    def test_version_attribute(self) -> None:
        coord = _make_coord()
        assert coord._version == "3.3"  # type: ignore[union-attr]

    def test_gw_id_attribute(self) -> None:
        coord = _make_coord()
        assert coord._gw_id == "gw001"  # type: ignore[union-attr]

    def test_ip_attribute(self) -> None:
        coord = _make_coord()
        assert coord._ip_address == "127.0.0.1"  # type: ignore[union-attr]


class TestCoordinatorOnFrame:
    def test_on_frame_updates_dps(self) -> None:
        from unittest.mock import patch

        coord = _make_coord()
        frame = MagicMock()
        frame.dps = {"dps": {"1": True}}

        with patch.object(coord.__class__, "async_set_updated_data"):  # type: ignore[union-attr]
            coord._on_frame(frame)  # type: ignore[union-attr]

        assert coord.state.dps["1"] is True  # type: ignore[union-attr]

    def test_on_frame_no_dps_attribute(self) -> None:
        coord = _make_coord()
        frame = MagicMock(spec=[])  # no .dps attribute

        coord._on_frame(frame)  # type: ignore[union-attr]  # must not raise

        assert coord.state.dps == {}  # type: ignore[union-attr]

    def test_on_frame_empty_dps_dict(self) -> None:
        coord = _make_coord()
        frame = MagicMock()
        frame.dps = {}  # empty dict, no "dps" key

        coord._on_frame(frame)  # type: ignore[union-attr]

        assert coord.state.dps == {}  # type: ignore[union-attr]

    def test_on_frame_non_dict_dps(self) -> None:
        coord = _make_coord()
        frame = MagicMock()
        frame.dps = "not_a_dict"

        coord._on_frame(frame)  # type: ignore[union-attr]

        assert coord.state.dps == {}  # type: ignore[union-attr]


class TestCoordinatorDoSendDps:
    @pytest.mark.asyncio
    async def test_do_send_dps_raises_when_writer_none(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coord()
        coord._writer = None  # type: ignore[union-attr]

        with pytest.raises(HomeAssistantError):
            await coord._do_send_dps({"1": True})  # type: ignore[union-attr]


class TestCoordinatorDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_when_no_writer(self) -> None:
        coord = _make_coord()
        coord._writer = None  # type: ignore[union-attr]
        await coord._disconnect()  # type: ignore[union-attr]
        assert coord._session_key is None  # type: ignore[union-attr]
        assert coord.state.available is False  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_disconnect_closes_writer(self) -> None:
        coord = _make_coord()
        writer = AsyncMock()
        writer.close = MagicMock()
        coord._writer = writer  # type: ignore[union-attr]
        coord._reader = MagicMock()  # type: ignore[union-attr]
        coord._session_key = b"key"  # type: ignore[union-attr]

        await coord._disconnect()  # type: ignore[union-attr]

        writer.close.assert_called_once()
        assert coord._writer is None  # type: ignore[union-attr]
        assert coord._session_key is None  # type: ignore[union-attr]
        assert coord.state.available is False  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_disconnect_suppresses_oserror(self) -> None:
        coord = _make_coord()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock(side_effect=OSError("pipe broken"))
        coord._writer = writer  # type: ignore[union-attr]
        coord._reader = MagicMock()  # type: ignore[union-attr]

        await coord._disconnect()  # type: ignore[union-attr]  # must not raise

        assert coord._writer is None  # type: ignore[union-attr]


class TestCoordinatorAuthRepair:
    def test_clear_auth_repair_issue(self) -> None:
        from unittest.mock import patch

        coord = _make_coord()
        coord.hass = coord._hass  # type: ignore[union-attr]  # set property

        with patch("homeassistant.helpers.issue_registry.async_delete_issue") as mock_delete:
            coord._clear_auth_repair_issue()  # type: ignore[union-attr]

        mock_delete.assert_called_once()

    def test_raise_auth_repair_issue(self) -> None:
        from unittest.mock import patch

        coord = _make_coord()
        coord.hass = coord._hass  # type: ignore[union-attr]  # set property

        with patch("homeassistant.helpers.issue_registry.async_create_issue") as mock_create:
            coord._raise_auth_repair_issue()  # type: ignore[union-attr]

        mock_create.assert_called_once()


class TestCoordinatorHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_heartbeat_breaks_when_writer_none(self) -> None:
        coord = _make_coord()
        coord._writer = None  # type: ignore[union-attr]

        with patch("custom_components.tuya_cloudless.coordinator.asyncio.sleep", new=AsyncMock()):
            await coord._heartbeat_loop()  # type: ignore[union-attr]
        # Should return without error

    @pytest.mark.asyncio
    async def test_heartbeat_sends_frame_then_exits(self) -> None:
        coord = _make_coord()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        coord._writer = writer  # type: ignore[union-attr]

        call_count = 0

        async def mock_sleep(_: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._writer = None  # type: ignore[union-attr]

        with patch(
            "custom_components.tuya_cloudless.coordinator.asyncio.sleep",
            side_effect=mock_sleep,
        ):
            await coord._heartbeat_loop()  # type: ignore[union-attr]

        writer.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_oserror_breaks_loop(self) -> None:
        coord = _make_coord()
        writer = MagicMock()
        writer.write = MagicMock(side_effect=OSError("broken pipe"))
        writer.drain = AsyncMock()
        coord._writer = writer  # type: ignore[union-attr]

        with patch("custom_components.tuya_cloudless.coordinator.asyncio.sleep", new=AsyncMock()):
            await coord._heartbeat_loop()  # type: ignore[union-attr]
        # Should return without raising


class TestCoordinatorNegotiateSessionKey:
    @pytest.mark.asyncio
    async def test_negotiate_all_attempts_fail_raises(self) -> None:
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coord()
        coord._negotiate_session_key_once = AsyncMock(  # type: ignore[union-attr]
            side_effect=TuyaCloudlessError("test failure")
        )

        with (
            patch("custom_components.tuya_cloudless.coordinator.asyncio.sleep", new=AsyncMock()),
            pytest.raises(TuyaCloudlessError, match="failed after"),
        ):
            await coord._negotiate_session_key(MagicMock(), MagicMock())  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_negotiate_succeeds_on_first_attempt(self) -> None:
        coord = _make_coord()
        expected_key = b"session_key_16by"
        coord._negotiate_session_key_once = AsyncMock(return_value=expected_key)  # type: ignore[union-attr]

        result = await coord._negotiate_session_key(MagicMock(), MagicMock())  # type: ignore[union-attr]
        assert result == expected_key


class TestCoordinatorFromConfigEntry:
    def test_from_config_entry_reads_options(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
            CONF_OPT_HEARTBEAT_INTERVAL,
            CONF_PROTOCOL_VERSION,
        )
        from custom_components.tuya_cloudless.coordinator import TuyaCloudlessCoordinator

        hass = MagicMock()
        hass.loop = asyncio.get_event_loop()

        entry = MagicMock()
        entry.entry_id = "e1"
        entry.data = {
            CONF_GW_ID: "gw001",
            CONF_IP_ADDRESS: "10.0.0.1",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_PROTOCOL_VERSION: "3.3",
        }
        entry.options = {CONF_OPT_HEARTBEAT_INTERVAL: 30}

        coord = TuyaCloudlessCoordinator.from_config_entry(hass, entry)
        assert coord._heartbeat_interval == 30
        assert coord._gw_id == "gw001"
