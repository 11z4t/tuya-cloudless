"""Unit tests for the TuyaCloudlessCoordinator (non-async paths).

Tests cover error handling in send_dps, state management, and helper methods.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_coord() -> object:
    """Create a coordinator without starting the connection loop."""
    from custom_components.tuya_cloudless.coordinator import TuyaCloudlessCoordinator

    hass = MagicMock()
    hass.loop = asyncio.get_event_loop()

    coord = TuyaCloudlessCoordinator.__new__(TuyaCloudlessCoordinator)
    coord._hass = hass
    coord.hass = hass
    coord._entry_id = "test"
    coord._gw_id = "gw001"
    coord.device_name = "gw001"
    coord.profile_name = ""
    coord._ip_address = "127.0.0.1"
    coord._local_key = b"0123456789abcdef"
    coord._version = "3.3"
    coord._port = 6668
    coord._heartbeat_interval = 20
    coord._command_timeout = 5.0
    coord._reconnect_max_delay = 300
    coord._send_lock = asyncio.Lock()
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

        coord = TuyaCloudlessCoordinator.from_config_entry(hass, entry, device_info=MagicMock())
        assert coord._heartbeat_interval == 30
        assert coord._gw_id == "gw001"


class TestAsyncStart:
    """Tests for async_start() — ROB-001 (PLAT-830) duplicate-call guard."""

    @pytest.mark.asyncio
    async def test_double_start_creates_single_task(self) -> None:
        """Two rapid async_start() calls must result in exactly one active _connect_task."""
        from custom_components.tuya_cloudless.coordinator import TuyaCloudlessCoordinator

        # Arrange: a coordinator with a hass mock whose async_create_task
        # creates real asyncio Tasks so that .done() behaves correctly.
        hass = MagicMock()

        created_tasks: list[asyncio.Task[None]] = []

        def _create_task(coro: object, **kw: object) -> asyncio.Task[None]:
            task: asyncio.Task[None] = asyncio.get_event_loop().create_task(coro)  # type: ignore[arg-type]
            created_tasks.append(task)
            return task

        hass.async_create_task = MagicMock(side_effect=_create_task)

        coord = TuyaCloudlessCoordinator(
            hass=hass,
            entry_id="test_rob001",
            gw_id="gw_rob001",
            ip_address="127.0.0.1",
            local_key="0123456789abcdef",
            version="3.3",
            device_info=MagicMock(),
        )

        # Patch _connection_loop with an AsyncMock that never returns, so the
        # task stays alive and the guard in async_start() can detect it.
        loop = asyncio.get_event_loop()
        never_done: asyncio.Future[None] = loop.create_future()

        async def _infinite_loop(self: object) -> None:
            await never_done

        with patch.object(TuyaCloudlessCoordinator, "_connection_loop", _infinite_loop):
            await coord.async_start()
            first_task = coord._connect_task

            # Give the event loop a chance to schedule the task (not strictly
            # necessary here but makes the test more realistic).
            await asyncio.sleep(0)

            await coord.async_start()  # second call — must be a no-op
            second_task = coord._connect_task

        # Assert: still exactly one task, it's the same object, and it is not done.
        assert first_task is not None, "_connect_task should not be None after async_start()"
        assert first_task is second_task, (
            "Second async_start() must not replace the existing task — "
            "only one connection loop should be running"
        )
        assert not first_task.done(), "The single connect task must still be running"
        # Confirm async_create_task was called exactly once (not twice)
        assert hass.async_create_task.call_count == 1, (
            f"Expected async_create_task to be called once, got {hass.async_create_task.call_count}"
        )

        # Clean up: cancel the lingering task so the event loop doesn't warn.
        first_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first_task
        if not never_done.done():
            never_done.cancel()


class TestCoordinatorInit:
    """Test TuyaCloudlessCoordinator.__init__ via the real constructor."""

    def _make_coord_direct(self, **kwargs: object) -> object:
        from custom_components.tuya_cloudless.coordinator import TuyaCloudlessCoordinator

        hass = MagicMock()
        hass.loop = asyncio.get_event_loop()
        defaults = {
            "hass": hass,
            "entry_id": "entry1",
            "gw_id": "gw_test",
            "ip_address": "10.0.0.1",
            "local_key": "0123456789abcdef",
            "version": "3.3",
            "device_info": MagicMock(),
        }
        defaults.update(kwargs)
        return TuyaCloudlessCoordinator(**defaults)  # type: ignore[arg-type]

    def test_gw_id_property(self) -> None:
        coord = self._make_coord_direct()
        assert coord.gw_id == "gw_test"  # type: ignore[union-attr]

    def test_version_property(self) -> None:
        coord = self._make_coord_direct()
        assert coord.version == "3.3"  # type: ignore[union-attr]

    def test_device_name_defaults_to_gw_id(self) -> None:
        coord = self._make_coord_direct()
        assert coord.device_name == "gw_test"  # type: ignore[union-attr]

    def test_profile_name_defaults_to_empty(self) -> None:
        coord = self._make_coord_direct()
        assert coord.profile_name == ""  # type: ignore[union-attr]

    def test_initial_state_unavailable(self) -> None:
        from custom_components.tuya_cloudless.coordinator import DeviceState

        coord = self._make_coord_direct()
        assert isinstance(coord.state, DeviceState)  # type: ignore[union-attr]
        assert coord.state.available is False  # type: ignore[union-attr]

    def test_local_key_encoded_from_str(self) -> None:
        coord = self._make_coord_direct(local_key="0123456789abcdef")
        assert isinstance(coord._local_key, bytes)  # type: ignore[union-attr]

    def test_heartbeat_interval_override(self) -> None:
        coord = self._make_coord_direct(heartbeat_interval=60)
        assert coord._heartbeat_interval == 60  # type: ignore[union-attr]

    def test_port_defaults(self) -> None:
        from custom_components.tuya_cloudless.const import DEFAULT_TCP_PORT

        coord = self._make_coord_direct()
        assert coord._port == DEFAULT_TCP_PORT  # type: ignore[union-attr]

    def test_writer_initially_none(self) -> None:
        coord = self._make_coord_direct()
        assert coord._writer is None  # type: ignore[union-attr]

    def test_session_key_initially_none(self) -> None:
        coord = self._make_coord_direct()
        assert coord._session_key is None  # type: ignore[union-attr]


class TestCoordinatorProperties:
    """Tests for public property accessors (lines 848-865)."""

    def test_tcp_connected_false_when_writer_none(self) -> None:
        """tcp_connected returns False when _writer is None (line 850)."""
        coord = _make_coord()
        coord._writer = None  # type: ignore[union-attr]
        assert coord.tcp_connected is False  # type: ignore[union-attr]

    def test_tcp_connected_true_when_writer_set(self) -> None:
        """tcp_connected returns True when _writer is not None (line 850)."""
        coord = _make_coord()
        coord._writer = MagicMock()  # type: ignore[union-attr]
        assert coord.tcp_connected is True  # type: ignore[union-attr]

    def test_sequence_counter_returns_current_value(self) -> None:
        """sequence_counter returns _sequence (line 855)."""
        coord = _make_coord()
        coord._sequence = 42  # type: ignore[union-attr]
        assert coord.sequence_counter == 42  # type: ignore[union-attr]

    def test_session_key_active_false_when_none(self) -> None:
        """session_key_active returns False when _session_key is None (line 860)."""
        coord = _make_coord()
        coord._session_key = None  # type: ignore[union-attr]
        assert coord.session_key_active is False  # type: ignore[union-attr]

    def test_session_key_active_true_when_set(self) -> None:
        """session_key_active returns True when _session_key is not None (line 860)."""
        coord = _make_coord()
        coord._session_key = b"some_key"  # type: ignore[union-attr]
        assert coord.session_key_active is True  # type: ignore[union-attr]

    def test_consecutive_decode_errors_returns_count(self) -> None:
        """consecutive_decode_errors returns _consecutive_decode_errors (line 865)."""
        coord = _make_coord()
        coord._consecutive_decode_errors = 7  # type: ignore[union-attr]
        assert coord.consecutive_decode_errors == 7  # type: ignore[union-attr]


class TestReceiveLoopBufErrors:
    """Tests for buffer-level decode error accumulation (lines 524-542)."""

    @pytest.mark.asyncio
    async def test_buf_errors_below_max_accumulate(self) -> None:
        """buf_errors > 0 but below _MAX_CONSECUTIVE_ERRORS: counter increments, loop continues."""
        coord = _make_coord()
        coord._writer = MagicMock()  # type: ignore[union-attr]
        coord._writer.close = MagicMock()

        call_count = 0

        class FakeMsgBuf:
            def __init__(self, **kwargs: object) -> None:
                pass

            def feed(self, chunk: bytes) -> None:
                pass

            @property
            def pending_bytes(self) -> int:
                return 0

            def messages(self) -> list:  # type: ignore[type-arg]
                return []

            def pop_error_count(self) -> int:
                return 1  # one buffer error per iteration

        reader = asyncio.StreamReader()

        async def fake_read(_n: int) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"\x01"  # first call returns data → triggers buf_errors path
            return b""  # second call returns empty → breaks loop

        reader.read = fake_read  # type: ignore[method-assign]

        with patch("tuya_cloudless.message.MessageBuffer", FakeMsgBuf):
            await coord._receive_loop(reader)  # type: ignore[union-attr]

        # After one iteration with 1 buf_error, counter should be 1
        assert coord._consecutive_decode_errors == 1  # type: ignore[union-attr]
        # writer was NOT closed (errors below max)
        coord._writer.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_buf_errors_at_max_triggers_reconnect(self) -> None:
        """buf_errors that push counter to _MAX_CONSECUTIVE_ERRORS closes writer and returns."""
        coord = _make_coord()
        coord._consecutive_decode_errors = 4  # type: ignore[union-attr]  # one away from max (5)
        writer = MagicMock()
        writer.close = MagicMock()
        coord._writer = writer  # type: ignore[union-attr]

        call_count = 0

        class FakeMsgBufMax:
            def __init__(self, **kwargs: object) -> None:
                pass

            def feed(self, chunk: bytes) -> None:
                pass

            @property
            def pending_bytes(self) -> int:
                return 0

            def messages(self) -> list:  # type: ignore[type-arg]
                return []

            def pop_error_count(self) -> int:
                return 1  # pushes total from 4 → 5 = _MAX_CONSECUTIVE_ERRORS

        reader = asyncio.StreamReader()

        async def fake_read_once(_n: int) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"\x01"
            return b""

        reader.read = fake_read_once  # type: ignore[method-assign]

        with (
            patch("tuya_cloudless.message.MessageBuffer", FakeMsgBufMax),
            patch("homeassistant.helpers.issue_registry.async_create_issue"),
        ):
            await coord._receive_loop(reader)  # type: ignore[union-attr]

        # writer.close() must have been called to trigger reconnect
        writer.close.assert_called_once()


class TestReceiveLoopCleanBatchDecay:
    """R30-1: Clean batch must decrement error counter by 1, not reset to 0.

    An adversarial device can suppress reconnect indefinitely by alternating a
    single good frame with ≤4 bad frames across separate TCP recv() calls.  With
    reset-to-zero semantics the counter would oscillate between 4 and 0 and never
    reach _MAX_CONSECUTIVE_ERRORS (5).  With decrement-by-1 semantics, each clean
    batch only reduces the counter by one step, so a sustained attack still
    accumulates to the reconnect threshold.
    """

    @pytest.mark.asyncio
    async def test_clean_batch_decrements_not_resets(self) -> None:
        """Error counter decrements by 1 on a clean batch, not resets to 0."""
        coord = _make_coord()
        coord._consecutive_decode_errors = 4  # type: ignore[union-attr]
        coord._writer = MagicMock()  # type: ignore[union-attr]
        coord._writer.close = MagicMock()

        call_count = 0

        class FakeMsgBufClean:
            def __init__(self, **kwargs: object) -> None:
                pass

            def feed(self, chunk: bytes) -> None:
                pass

            @property
            def pending_bytes(self) -> int:
                return 0

            def messages(self) -> list:  # type: ignore[type-arg]
                return []

            def pop_error_count(self) -> int:
                return 0  # no errors — clean batch

        reader = asyncio.StreamReader()

        async def fake_read(_n: int) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"\x01"  # one clean recv
            return b""

        reader.read = fake_read  # type: ignore[method-assign]

        with patch("tuya_cloudless.message.MessageBuffer", FakeMsgBufClean):
            await coord._receive_loop(reader)  # type: ignore[union-attr]

        # Counter must decrement from 4 → 3 (NOT reset to 0)
        assert coord._consecutive_decode_errors == 3  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_attack_pattern_good_bad_triggers_reconnect(self) -> None:
        """Alternating good+bad batches still trigger reconnect (R30-1).

        Attack: recv(1 good), recv(4 bad), recv(1 good), recv(4 bad)
        With decrement-by-1 the counter reaches 5 after two cycles.
        """
        coord = _make_coord()
        coord._consecutive_decode_errors = 0  # type: ignore[union-attr]
        writer = MagicMock()
        writer.close = MagicMock()
        coord._writer = writer  # type: ignore[union-attr]

        recv_count = 0
        # Sequence: clean, 4 errors, clean, 4 errors → counter: 0,4,3,7 → reconnect
        recv_sequence = [
            0,  # clean batch  → 0 - 1 + 1 = 0 (no underflow; starts at 0)
            4,  # 4 errors     → 0 + 4 = 4
            0,  # clean batch  → 4 - 1 = 3
            4,  # 4 errors     → 3 + 4 = 7 → triggers reconnect
        ]

        class FakeMsgBufAttack:
            def __init__(self, **kwargs: object) -> None:
                pass

            def feed(self, chunk: bytes) -> None:
                pass

            @property
            def pending_bytes(self) -> int:
                return 0

            def messages(self) -> list:  # type: ignore[type-arg]
                return []

            def pop_error_count(self) -> int:
                return recv_sequence[recv_count - 1]

        reader = asyncio.StreamReader()

        async def fake_read(_n: int) -> bytes:
            nonlocal recv_count
            recv_count += 1
            if recv_count <= len(recv_sequence):
                return b"\x01"
            return b""

        reader.read = fake_read  # type: ignore[method-assign]

        with (
            patch("tuya_cloudless.message.MessageBuffer", FakeMsgBufAttack),
            patch("homeassistant.helpers.issue_registry.async_create_issue"),
        ):
            await coord._receive_loop(reader)  # type: ignore[union-attr]

        # writer.close() must have been called to trigger reconnect
        writer.close.assert_called_once()


class TestNegotiateSessionKeyOnce:
    """Tests for _negotiate_session_key_once (lines 686-753)."""

    @pytest.mark.asyncio
    async def test_raises_on_oversized_payload(self) -> None:
        """Frame payload length > MAX_PAYLOAD_SIZE must raise TuyaCloudlessError (line 699)."""
        import struct as _struct

        from tuya_cloudless.const import FRAME_HEADER_SIZE, MAX_PAYLOAD_SIZE
        from tuya_cloudless.exceptions import TuyaCloudlessError

        coord = _make_coord()

        # Build a fake header where bytes 12-16 encode a huge payload length
        oversized_len = MAX_PAYLOAD_SIZE + 1
        header = (
            b"\x00" * 12 + _struct.pack(">I", oversized_len) + b"\x00" * (FRAME_HEADER_SIZE - 16)
        )

        reader = MagicMock()
        reader.readexactly = AsyncMock(return_value=header)

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        # generate_ecdh_keypair is imported locally inside the method from tuya_cloudless.crypto
        mock_keypair = MagicMock()
        mock_keypair.public_key_bytes = b"\x00" * 32

        with (
            patch(
                "tuya_cloudless.crypto.generate_ecdh_keypair",
                return_value=mock_keypair,
            ),
            patch(
                "tuya_cloudless.protocol.encode_session_key_start",
                return_value=b"\x00" * 20,
            ),
            pytest.raises(TuyaCloudlessError, match="too large"),
        ):
            await coord._negotiate_session_key_once(reader, writer)  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_full_negotiation_success(self) -> None:
        """Successful negotiation returns a session key bytes object (lines 736-753)."""
        import struct as _struct

        from tuya_cloudless.const import FRAME_HEADER_SIZE

        coord = _make_coord()
        coord._version = "3.4"  # type: ignore[union-attr]

        # Build a valid-looking header with a small payload length
        payload_len = 64
        header = b"\x00" * 12 + _struct.pack(">I", payload_len) + b"\x00" * (FRAME_HEADER_SIZE - 16)
        payload_bytes = b"\x00" * payload_len

        call_count = 0

        async def fake_readexactly(n: int) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return header
            return payload_bytes

        reader = MagicMock()
        reader.readexactly = AsyncMock(side_effect=fake_readexactly)

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()

        from tuya_cloudless.protocol import TuyaFrame

        mock_frame = TuyaFrame(sequence=1, command=0x03, version="3.3", payload=b"\xab" * 32)
        fake_session_key = b"\xff" * 16

        mock_keypair = MagicMock()
        mock_keypair.public_key_bytes = b"\x00" * 32
        mock_keypair.private_key = b"\x01" * 32

        with (
            patch(
                "tuya_cloudless.crypto.generate_ecdh_keypair",
                return_value=mock_keypair,
            ),
            patch(
                "tuya_cloudless.protocol.encode_session_key_start",
                return_value=b"\x00" * 20,
            ),
            patch("tuya_cloudless.protocol.split_frames", return_value=([b"rawframe"], b"")),
            patch("tuya_cloudless.protocol.decode_frame", return_value=mock_frame),
            patch(
                "tuya_cloudless.crypto.derive_session_key",
                return_value=fake_session_key,
            ),
            patch(
                "tuya_cloudless.protocol.encode_session_key_finish",
                return_value=b"\x00" * 20,
            ),
        ):
            result = await coord._negotiate_session_key_once(reader, writer)  # type: ignore[union-attr]

        assert result == fake_session_key
