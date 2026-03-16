"""Tests for custom_components.tuya_cloudless.coordinator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.tuya_cloudless.coordinator import (
    _MAX_BUFFER_BYTES,
    _MAX_CONSECUTIVE_ERRORS,
    DeviceState,
    TuyaCloudlessCoordinator,
)

# ── DeviceState ────────────────────────────────────────────────────────────────


class TestDeviceState:
    def test_defaults(self) -> None:
        state = DeviceState()
        assert state.available is False
        assert state.dps == {}
        assert state.last_seen is None
        assert state.reconnect_count == 0
        assert state.last_error is None

    def test_custom_values(self) -> None:
        ts = datetime.now(UTC)
        state = DeviceState(
            available=True,
            dps={"1": True},
            last_seen=ts,
            reconnect_count=3,
            last_error="test error",
        )
        assert state.available is True
        assert state.dps == {"1": True}
        assert state.last_seen == ts
        assert state.reconnect_count == 3
        assert state.last_error == "test error"


# ── TuyaCloudlessCoordinator ──────────────────────────────────────────────────


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kw: asyncio.ensure_future(coro))
    return hass


def _make_coordinator(
    hass: MagicMock | None = None,
    gw_id: str = "gw001",
    ip: str = "192.168.1.42",
    local_key: str = "0123456789abcdef",
    version: str = "3.3",
    port: int = 6668,
) -> TuyaCloudlessCoordinator:
    return TuyaCloudlessCoordinator(
        hass=hass or _make_hass(),
        entry_id="test_entry",
        gw_id=gw_id,
        ip_address=ip,
        local_key=local_key,
        version=version,
        port=port,
    )


class TestCoordinatorInit:
    def test_initial_state(self) -> None:
        coord = _make_coordinator()
        assert coord.state.available is False
        assert coord.state.dps == {}
        assert coord._session_key is None
        assert coord._sequence == 0

    def test_local_key_encoding(self) -> None:
        coord = _make_coordinator(local_key="0123456789abcdef")
        assert coord._local_key == b"0123456789abcdef"

    def test_port_parameter(self) -> None:
        coord = _make_coordinator(port=9999)
        assert coord._port == 9999

    def test_custom_timeouts(self) -> None:
        coord = TuyaCloudlessCoordinator(
            hass=_make_hass(),
            entry_id="e",
            gw_id="g",
            ip_address="1.2.3.4",
            local_key="0123456789abcdef",
            version="3.3",
            heartbeat_interval=30.0,
            command_timeout=10.0,
            reconnect_max_delay=600.0,
        )
        assert coord._heartbeat_interval == 30.0
        assert coord._command_timeout == 10.0
        assert coord._reconnect_max_delay == 600.0


class TestCoordinatorConstants:
    def test_max_consecutive_errors(self) -> None:
        assert _MAX_CONSECUTIVE_ERRORS == 5

    def test_max_buffer_bytes(self) -> None:
        assert _MAX_BUFFER_BYTES == 65536


class TestNextSequence:
    def test_increments(self) -> None:
        coord = _make_coordinator()
        seq1 = coord._next_sequence()
        seq2 = coord._next_sequence()
        assert seq2 == seq1 + 1

    def test_wraps_at_32bit(self) -> None:
        coord = _make_coordinator()
        coord._sequence = 0xFFFFFFFE
        seq = coord._next_sequence()
        assert seq == 0xFFFFFFFF
        seq = coord._next_sequence()
        assert seq == 0  # wrapped


class TestOnFrame:
    def test_updates_dps(self) -> None:
        coord = _make_coordinator()
        frame = MagicMock()
        frame.dps = {"dps": {"1": True, "2": 50}}
        coord._on_frame(frame)
        assert coord.state.dps == {"1": True, "2": 50}
        assert coord.state.last_seen is not None

    def test_ignores_empty_dps(self) -> None:
        coord = _make_coordinator()
        frame = MagicMock()
        frame.dps = {"dps": {}}
        coord._on_frame(frame)
        assert coord.state.dps == {}
        assert coord.state.last_seen is None

    def test_ignores_non_dict_payload(self) -> None:
        coord = _make_coordinator()
        frame = MagicMock()
        frame.dps = "not a dict"
        coord._on_frame(frame)
        assert coord.state.dps == {}

    def test_ignores_no_dps_attr(self) -> None:
        coord = _make_coordinator()
        frame = MagicMock(spec=[])  # No dps attribute
        coord._on_frame(frame)
        assert coord.state.dps == {}

    def test_merges_dps(self) -> None:
        coord = _make_coordinator()
        coord.state.dps = {"1": False}
        frame = MagicMock()
        frame.dps = {"dps": {"2": 42}}
        coord._on_frame(frame)
        assert coord.state.dps == {"1": False, "2": 42}


class TestSendDps:
    @pytest.mark.asyncio
    async def test_raises_when_unavailable(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        coord.state.available = False
        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})

    @pytest.mark.asyncio
    async def test_raises_when_no_writer(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        coord.state.available = True
        coord._writer = None
        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})


class TestFromConfigEntry:
    def test_creates_coordinator(self) -> None:
        hass = _make_hass()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {
            "gw_id": "gw001",
            "ip_address": "192.168.1.42",
            "local_key": "0123456789abcdef",
            "protocol_version": "3.3",
        }
        entry.options = {}

        coord = TuyaCloudlessCoordinator.from_config_entry(hass, entry)
        assert coord._gw_id == "gw001"
        assert coord._ip == "192.168.1.42"
        assert coord._version == "3.3"

    def test_uses_options_overrides(self) -> None:
        hass = _make_hass()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {
            "gw_id": "gw001",
            "ip_address": "192.168.1.42",
            "local_key": "0123456789abcdef",
            "protocol_version": "3.3",
        }
        entry.options = {
            "heartbeat_interval": 30,
            "command_timeout": 10,
            "reconnect_max_delay": 600,
        }

        coord = TuyaCloudlessCoordinator.from_config_entry(hass, entry)
        assert coord._heartbeat_interval == 30.0
        assert coord._command_timeout == 10.0
        assert coord._reconnect_max_delay == 600.0

    def test_default_version_33(self) -> None:
        hass = _make_hass()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {
            "gw_id": "gw001",
            "ip_address": "192.168.1.42",
            "local_key": "0123456789abcdef",
        }
        entry.options = {}

        coord = TuyaCloudlessCoordinator.from_config_entry(hass, entry)
        assert coord._version == "3.3"


class TestRepairIssues:
    def test_raise_auth_repair_issue(self) -> None:
        coord = _make_coordinator()
        with patch("homeassistant.helpers.issue_registry.async_create_issue") as mock_create:
            coord._raise_auth_repair_issue()
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["is_fixable"] is True
            assert "auth_failed" in call_kwargs["translation_key"]

    def test_clear_auth_repair_issue(self) -> None:
        coord = _make_coordinator()
        with patch("homeassistant.helpers.issue_registry.async_delete_issue") as mock_delete:
            coord._clear_auth_repair_issue()
            mock_delete.assert_called_once()

    def test_raise_connectivity_repair_issue(self) -> None:
        coord = _make_coordinator()
        with patch("homeassistant.helpers.issue_registry.async_create_issue") as mock_create:
            coord._raise_connectivity_repair_issue()
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["is_fixable"] is True
            assert call_kwargs["translation_key"] == "connectivity"
            assert "connectivity_" in mock_create.call_args[0][2]

    def test_clear_connectivity_repair_issue(self) -> None:
        coord = _make_coordinator()
        with patch("homeassistant.helpers.issue_registry.async_delete_issue") as mock_delete:
            coord._clear_connectivity_repair_issue()
            mock_delete.assert_called_once()
            issue_id = mock_delete.call_args[0][2]
            assert issue_id.startswith("connectivity_")


class TestConnectivityRepair:
    @pytest.mark.asyncio
    async def test_connectivity_issue_raised_after_threshold(self) -> None:
        """After CONNECTIVITY_ISSUE_THRESHOLD failures the repair issue should be created."""
        from custom_components.tuya_cloudless.const import CONNECTIVITY_ISSUE_THRESHOLD

        coord = _make_coordinator()
        call_count = 0

        async def mock_connect() -> None:
            nonlocal call_count
            call_count += 1
            if call_count <= CONNECTIVITY_ISSUE_THRESHOLD + 1:
                raise OSError("Connection refused")
            raise asyncio.CancelledError

        coord._connect = mock_connect

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_raise_connectivity_repair_issue") as mock_raise,
            patch.object(coord, "_clear_connectivity_repair_issue"),
        ):
            await coord._connection_loop()

        mock_raise.assert_called()

    @pytest.mark.asyncio
    async def test_connectivity_issue_not_raised_below_threshold(self) -> None:
        """Below the threshold, no repair issue should be created."""
        from custom_components.tuya_cloudless.const import CONNECTIVITY_ISSUE_THRESHOLD

        coord = _make_coordinator()
        call_count = 0

        async def mock_connect() -> None:
            nonlocal call_count
            call_count += 1
            if call_count < CONNECTIVITY_ISSUE_THRESHOLD:
                raise OSError("Connection refused")
            raise asyncio.CancelledError

        coord._connect = mock_connect

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_raise_connectivity_repair_issue") as mock_raise,
            patch.object(coord, "_clear_connectivity_repair_issue"),
        ):
            await coord._connection_loop()

        mock_raise.assert_not_called()

    @pytest.mark.asyncio
    async def test_connectivity_issue_cleared_on_success(self) -> None:
        """Successful connection should clear any existing connectivity repair issue."""
        coord = _make_coordinator()
        call_count = 0

        async def mock_connect() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return  # Success on first attempt
            raise asyncio.CancelledError

        coord._connect = mock_connect

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_raise_connectivity_repair_issue"),
            patch.object(coord, "_clear_connectivity_repair_issue") as mock_clear,
        ):
            await coord._connection_loop()

        mock_clear.assert_called()

    @pytest.mark.asyncio
    async def test_consecutive_failures_reset_on_success(self) -> None:
        """Failure counter should reset to zero after a successful connection."""
        coord = _make_coordinator()
        coord._consecutive_connection_failures = 5
        call_count = 0

        async def mock_connect() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return  # Success
            raise asyncio.CancelledError

        coord._connect = mock_connect

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(coord, "_raise_connectivity_repair_issue"),
            patch.object(coord, "_clear_connectivity_repair_issue"),
        ):
            await coord._connection_loop()

        assert coord._consecutive_connection_failures == 0

    def test_connectivity_issue_threshold_constant(self) -> None:
        from custom_components.tuya_cloudless.const import CONNECTIVITY_ISSUE_THRESHOLD

        assert CONNECTIVITY_ISSUE_THRESHOLD == 10


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self) -> None:
        coord = _make_coordinator()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        coord._writer = writer
        coord._session_key = b"fake_key_1234567"
        coord.state.available = True

        await coord._disconnect()
        assert coord._writer is None
        assert coord._reader is None
        assert coord._session_key is None
        assert coord.state.available is False

    @pytest.mark.asyncio
    async def test_disconnect_handles_no_writer(self) -> None:
        coord = _make_coordinator()
        coord._writer = None
        await coord._disconnect()  # Should not raise


class TestAsyncStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self) -> None:
        coord = _make_coordinator()

        # Create real asyncio tasks that can be cancelled and awaited
        async def noop() -> None:
            await asyncio.sleep(100)

        for attr in ("_connect_task", "_heartbeat_task", "_read_task"):
            task = asyncio.create_task(noop())
            setattr(coord, attr, task)

        coord._disconnect = AsyncMock()
        await coord.async_stop()
        coord._disconnect.assert_awaited_once()

        # All tasks should be cancelled
        for attr in ("_connect_task", "_heartbeat_task", "_read_task"):
            task = getattr(coord, attr)
            assert task.cancelled()


class TestConnectionLoop:
    @pytest.mark.asyncio
    async def test_reconnect_on_oserror(self) -> None:
        """Connection loop should retry on OSError."""
        coord = _make_coordinator()
        call_count = 0

        async def mock_connect() -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("Connection refused")
            # Stop loop by raising CancelledError (caught by loop → return)
            raise asyncio.CancelledError

        coord._connect = mock_connect

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coord._connection_loop()

        assert call_count == 3
        assert coord.state.reconnect_count >= 1

    @pytest.mark.asyncio
    async def test_reconnect_on_timeout(self) -> None:
        coord = _make_coordinator()
        call_count = 0

        async def mock_connect() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timed out")
            raise asyncio.CancelledError

        coord._connect = mock_connect

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coord._connection_loop()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cancelled_stops_loop(self) -> None:
        coord = _make_coordinator()

        async def mock_connect() -> None:
            raise asyncio.CancelledError

        coord._connect = mock_connect
        # Should return cleanly
        await coord._connection_loop()


class TestHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_heartbeat_stops_when_no_writer(self) -> None:
        coord = _make_coordinator()
        coord._writer = None
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coord._heartbeat_loop()

    @pytest.mark.asyncio
    async def test_heartbeat_sends_frame(self) -> None:
        coord = _make_coordinator()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        coord._writer = writer
        call_count = 0

        async def mock_sleep(secs: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._writer = None  # Stop loop

        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("tuya_cloudless.protocol.encode_heartbeat", return_value=b"hb"),
        ):
            await coord._heartbeat_loop()

        writer.write.assert_called()

    @pytest.mark.asyncio
    async def test_heartbeat_oserror_stops_loop(self) -> None:
        coord = _make_coordinator()
        writer = MagicMock()
        writer.write = MagicMock(side_effect=OSError("broken"))
        writer.drain = AsyncMock()
        coord._writer = writer

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coord._heartbeat_loop()


class TestDoSendDps:
    @pytest.mark.asyncio
    async def test_sends_encoded_frame(self) -> None:
        coord = _make_coordinator()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        coord._writer = writer

        with patch("tuya_cloudless.protocol.encode_control", return_value=b"ctrl"):
            await coord._do_send_dps({"1": True})

        writer.write.assert_called_once_with(b"ctrl")
        writer.drain.assert_awaited_once()


class TestAsyncSendDpsTimeout:
    @pytest.mark.asyncio
    async def test_timeout_raises_ha_error(self) -> None:
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        coord.state.available = True
        coord._command_timeout = 0.01
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        coord._writer = writer

        async def slow_send(dps: Any) -> None:
            await asyncio.sleep(10)

        coord._do_send_dps = slow_send

        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_v33_no_session_key(self) -> None:
        """v3.3 connect should NOT negotiate session key."""
        coord = _make_coordinator(version="3.3")

        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        writer.drain = AsyncMock()

        async def mock_receive_loop(r: Any) -> None:
            pass  # Return immediately

        coord._receive_loop = mock_receive_loop
        coord._disconnect = AsyncMock()

        with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = (reader, writer)
            await coord._connect()

        assert coord._session_key is None
        assert coord.state.available is True

    @pytest.mark.asyncio
    async def test_connect_v34_negotiates_session_key(self) -> None:
        """v3.4 connect should negotiate session key."""
        coord = _make_coordinator(version="3.4")

        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        writer.drain = AsyncMock()

        async def mock_receive_loop(r: Any) -> None:
            pass

        coord._receive_loop = mock_receive_loop
        coord._disconnect = AsyncMock()
        coord._negotiate_session_key = AsyncMock(return_value=b"session_key12345")

        with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = (reader, writer)
            await coord._connect()

        assert coord._session_key == b"session_key12345"

    @pytest.mark.asyncio
    async def test_connect_v34_negotiation_failure(self) -> None:
        """v3.4 connect should raise on session negotiation failure."""
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coordinator(version="3.4")

        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        coord._negotiate_session_key = AsyncMock(
            side_effect=TuyaCloudlessError("negotiation failed")
        )

        with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = (reader, writer)
            with pytest.raises(TuyaCloudlessError):
                await coord._connect()


class TestReceiveLoop:
    @pytest.mark.asyncio
    async def test_eof_stops_loop(self) -> None:
        coord = _make_coordinator()
        reader = AsyncMock()
        reader.read = AsyncMock(return_value=b"")  # EOF

        with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=b""):
            await coord._receive_loop(reader)

    @pytest.mark.asyncio
    async def test_buffer_overflow_closes_writer(self) -> None:
        coord = _make_coordinator()
        writer = MagicMock()
        writer.close = MagicMock()
        coord._writer = writer

        reader = AsyncMock()
        big_chunk = b"\x00" * (_MAX_BUFFER_BYTES + 1)

        call_count = 0

        async def mock_wait_for(coro: Any, timeout: float = 0) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return big_chunk
            return b""

        with patch("asyncio.wait_for", side_effect=mock_wait_for):
            await coord._receive_loop(reader)

        writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_decode_errors_trigger_reconnect(self) -> None:
        """5 consecutive decrypt errors should close the connection."""
        from tuya_cloudless.exceptions import TuyaCloudlessError
        from tuya_cloudless.message import MessageBuffer

        coord = _make_coordinator()
        writer = MagicMock()
        writer.close = MagicMock()
        coord._writer = writer
        coord._consecutive_decode_errors = 0

        fake_chunk = b"some_data"
        call_count = 0

        async def mock_wait_for(coro: Any, timeout: float = 0) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return fake_chunk
            return b""

        # Build fake TuyaMessage objects that will fail decryption
        fake_msg = MagicMock()
        fake_msg.payload = b"encrypted_payload"

        with (
            patch("asyncio.wait_for", side_effect=mock_wait_for),
            patch.object(
                MessageBuffer,
                "messages",
                return_value=[fake_msg] * _MAX_CONSECUTIVE_ERRORS,
            ),
            patch(
                "tuya_cloudless.crypto.decrypt_payload",
                side_effect=TuyaCloudlessError("bad frame"),
            ),
            patch.object(coord, "_raise_auth_repair_issue"),
        ):
            await coord._receive_loop(AsyncMock())

        writer.close.assert_called()

    @pytest.mark.asyncio
    async def test_successful_frame_clears_errors(self) -> None:
        from tuya_cloudless.message import MessageBuffer

        coord = _make_coordinator()
        coord._consecutive_decode_errors = 3

        fake_msg = MagicMock()
        fake_msg.payload = b'{"dps": {"1": true}}'

        call_count = 0

        async def mock_wait_for(coro: Any, timeout: float = 0) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"data"
            return b""  # EOF

        with (
            patch("asyncio.wait_for", side_effect=mock_wait_for),
            patch.object(MessageBuffer, "messages", return_value=[fake_msg]),
            patch(
                "tuya_cloudless.crypto.decrypt_payload",
                return_value=b'{"dps": {"1": true}}',
            ),
            patch.object(coord, "_clear_auth_repair_issue"),
        ):
            await coord._receive_loop(AsyncMock())

        assert coord._consecutive_decode_errors == 0
        assert coord.state.dps["1"] is True

    @pytest.mark.asyncio
    async def test_timeout_continues(self) -> None:
        """Receive timeout should not break the loop."""
        coord = _make_coordinator()
        call_count = 0

        async def mock_wait_for(coro: Any, timeout: float = 0) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError
            return b""  # EOF

        with patch("asyncio.wait_for", side_effect=mock_wait_for):
            await coord._receive_loop(AsyncMock())

    @pytest.mark.asyncio
    async def test_partial_frame_reassembly(self) -> None:
        """MessageBuffer must reassemble a frame split across two TCP reads."""
        from tuya_cloudless.const import CMD_STATUS
        from tuya_cloudless.protocol import encode_frame

        coord = _make_coordinator(version="3.1")

        # Build a valid v3.1 frame (plaintext, no encryption)
        local_key = coord._local_key
        valid_frame = encode_frame(
            CMD_STATUS,
            b'{"dps": {"1": true}}',
            sequence=1,
            version="3.1",
            local_key=local_key,
        )

        # Split the frame into two halves to simulate TCP fragmentation
        mid = len(valid_frame) // 2
        first_half = valid_frame[:mid]
        second_half = valid_frame[mid:]

        call_count = 0

        async def mock_wait_for(coro: Any, timeout: float = 0) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_half
            if call_count == 2:
                return second_half
            return b""  # EOF

        with patch("asyncio.wait_for", side_effect=mock_wait_for):
            await coord._receive_loop(AsyncMock())

        # The frame should have been reassembled and DPS state updated
        assert coord.state.dps.get("1") is True


class TestNegotiateSessionKey:
    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        """Should retry up to 3 times before raising."""
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coordinator(version="3.4")
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        coord._negotiate_session_key_once = AsyncMock(side_effect=TuyaCloudlessError("fail"))

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(TuyaCloudlessError, match="failed after 3 attempts"),
        ):
            await coord._negotiate_session_key(reader, writer)

        assert coord._negotiate_session_key_once.await_count == 3

    @pytest.mark.asyncio
    async def test_success_on_first_try(self) -> None:
        coord = _make_coordinator(version="3.4")
        reader = AsyncMock()
        writer = MagicMock()

        coord._negotiate_session_key_once = AsyncMock(return_value=b"session1234abcde")

        result = await coord._negotiate_session_key(reader, writer)
        assert result == b"session1234abcde"
        assert coord._negotiate_session_key_once.await_count == 1

    @pytest.mark.asyncio
    async def test_success_on_retry(self) -> None:
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coordinator(version="3.4")
        reader = AsyncMock()
        writer = MagicMock()

        call_count = 0

        async def mock_negotiate_once(r: Any, w: Any) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TuyaCloudlessError("fail")
            return b"session1234abcde"

        coord._negotiate_session_key_once = mock_negotiate_once

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await coord._negotiate_session_key(reader, writer)
        assert result == b"session1234abcde"


class TestNegotiateSessionKeyOnce:
    @pytest.mark.asyncio
    async def test_timeout_waiting_for_response(self) -> None:
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coordinator(version="3.4")
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        # Mock the ECDH keypair generation
        mock_keypair = MagicMock()
        mock_keypair.public_key_bytes = b"\x42" * 32

        with (
            patch(
                "tuya_cloudless.crypto.generate_ecdh_keypair",
                return_value=mock_keypair,
            ),
            patch(
                "tuya_cloudless.protocol.encode_session_key_start",
                return_value=b"start_frame",
            ),
            patch(
                "asyncio.wait_for",
                side_effect=TimeoutError,
            ),
            pytest.raises(TuyaCloudlessError, match="timed out"),
        ):
            await coord._negotiate_session_key_once(reader, writer)

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coordinator(version="3.4")
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        mock_keypair = MagicMock()
        mock_keypair.public_key_bytes = b"\x42" * 32

        with (
            patch(
                "tuya_cloudless.crypto.generate_ecdh_keypair",
                return_value=mock_keypair,
            ),
            patch(
                "tuya_cloudless.protocol.encode_session_key_start",
                return_value=b"start_frame",
            ),
            patch(
                "asyncio.wait_for",
                new_callable=AsyncMock,
                return_value=b"",
            ),
            pytest.raises(TuyaCloudlessError, match="closed connection"),
        ):
            await coord._negotiate_session_key_once(reader, writer)

    @pytest.mark.asyncio
    async def test_no_frames_in_response(self) -> None:
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coordinator(version="3.4")
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        mock_keypair = MagicMock()
        mock_keypair.public_key_bytes = b"\x42" * 32

        with (
            patch(
                "tuya_cloudless.crypto.generate_ecdh_keypair",
                return_value=mock_keypair,
            ),
            patch(
                "tuya_cloudless.protocol.encode_session_key_start",
                return_value=b"start_frame",
            ),
            patch(
                "asyncio.wait_for",
                new_callable=AsyncMock,
                return_value=b"some_data",
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([], b"leftover"),
            ),
            pytest.raises(TuyaCloudlessError, match="parse"),
        ):
            await coord._negotiate_session_key_once(reader, writer)

    @pytest.mark.asyncio
    async def test_short_device_pubkey(self) -> None:
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coordinator(version="3.4")
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        mock_keypair = MagicMock()
        mock_keypair.public_key_bytes = b"\x42" * 32

        # Create a mock frame with short payload
        mock_frame = MagicMock()
        mock_frame.payload = b"\x00" * 10  # Too short

        with (
            patch(
                "tuya_cloudless.crypto.generate_ecdh_keypair",
                return_value=mock_keypair,
            ),
            patch(
                "tuya_cloudless.protocol.encode_session_key_start",
                return_value=b"start_frame",
            ),
            patch(
                "asyncio.wait_for",
                new_callable=AsyncMock,
                return_value=b"some_data",
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=mock_frame,
            ),
            pytest.raises(TuyaCloudlessError, match="too short"),
        ):
            await coord._negotiate_session_key_once(reader, writer)
