"""Concurrency and stress tests for TuyaCloudlessCoordinator (PLAT-730).

Verifies that:
- Concurrent async_send_dps calls are serialised by _send_lock.
- Multiple concurrent callers all complete successfully.
- _send_lock is released even when _do_send_dps raises.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.tuya_cloudless.coordinator import TuyaCloudlessCoordinator

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kw: asyncio.ensure_future(coro))
    return hass


def _make_coordinator(
    gw_id: str = "gw001",
    version: str = "3.3",
) -> TuyaCloudlessCoordinator:
    coord = TuyaCloudlessCoordinator(
        hass=_make_hass(),
        entry_id="test_entry",
        gw_id=gw_id,
        ip_address="192.168.1.42",
        local_key="0123456789abcdef",
        version=version,
        device_info=MagicMock(),
    )
    # Set up a mock writer so the device appears available
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    coord._writer = writer
    coord.state.available = True
    return coord


# ── Serialisation ──────────────────────────────────────────────────────────────


class TestSendLockSerialisation:
    @pytest.mark.asyncio
    async def test_two_concurrent_sends_are_serialised(self) -> None:
        """Two concurrent async_send_dps calls must never interleave."""
        coord = _make_coordinator()
        execution_order: list[int] = []

        async def tracked_send(dps: dict[str, Any]) -> None:
            slot = dps["slot"]
            execution_order.append(slot)
            await asyncio.sleep(0.02)
            execution_order.append(-slot)

        coord._do_send_dps = tracked_send  # type: ignore[method-assign]

        await asyncio.gather(
            coord.async_send_dps({"slot": 1}),
            coord.async_send_dps({"slot": 2}),
        )

        # Serialised execution: one send fully completes before the next starts.
        # Either [1, -1, 2, -2] or [2, -2, 1, -1] are valid.
        assert execution_order in ([1, -1, 2, -2], [2, -2, 1, -1]), execution_order

    @pytest.mark.asyncio
    async def test_three_concurrent_sends_are_serialised(self) -> None:
        """Three concurrent sends must not interleave."""
        coord = _make_coordinator()
        active_at_same_time: list[int] = []
        max_concurrent = 0

        async def tracked_send(dps: dict[str, Any]) -> None:
            nonlocal max_concurrent
            active_at_same_time.append(1)
            max_concurrent = max(max_concurrent, sum(active_at_same_time))
            await asyncio.sleep(0.01)
            active_at_same_time.pop()

        coord._do_send_dps = tracked_send  # type: ignore[method-assign]

        await asyncio.gather(
            coord.async_send_dps({"dp": 1}),
            coord.async_send_dps({"dp": 2}),
            coord.async_send_dps({"dp": 3}),
        )

        # Lock must prevent more than one send running at any moment
        assert max_concurrent == 1

    @pytest.mark.asyncio
    async def test_send_order_is_fifo_within_single_task_batch(self) -> None:
        """All sends in a gather batch complete — order may vary but none are lost."""
        coord = _make_coordinator()
        completed: list[int] = []

        async def record_send(dps: dict[str, Any]) -> None:
            await asyncio.sleep(0)
            completed.append(dps["n"])

        coord._do_send_dps = record_send  # type: ignore[method-assign]

        await asyncio.gather(*[coord.async_send_dps({"n": i}) for i in range(5)])

        assert sorted(completed) == list(range(5))


# ── All callers complete ────────────────────────────────────────────────────────


class TestAllCallersComplete:
    @pytest.mark.asyncio
    async def test_ten_concurrent_sends_all_succeed(self) -> None:
        """10 concurrent callers must all complete without any being dropped."""
        coord = _make_coordinator()
        results: list[int] = []

        async def record_send(dps: dict[str, Any]) -> None:
            await asyncio.sleep(0.005)
            results.append(dps["id"])

        coord._do_send_dps = record_send  # type: ignore[method-assign]

        await asyncio.gather(*[coord.async_send_dps({"id": i}) for i in range(10)])

        assert len(results) == 10
        assert sorted(results) == list(range(10))

    @pytest.mark.asyncio
    async def test_concurrent_sends_with_varying_delays(self) -> None:
        """Sends with different delays all complete correctly."""
        coord = _make_coordinator()
        results: list[str] = []
        delays = [0.03, 0.01, 0.02, 0.005, 0.015]

        async def delayed_send(dps: dict[str, Any]) -> None:
            await asyncio.sleep(dps["delay"])
            results.append(dps["name"])

        coord._do_send_dps = delayed_send  # type: ignore[method-assign]

        await asyncio.gather(
            *[coord.async_send_dps({"delay": d, "name": f"cmd{i}"}) for i, d in enumerate(delays)]
        )

        # All sends must have completed
        assert len(results) == len(delays)
        assert sorted(results) == sorted(f"cmd{i}" for i in range(len(delays)))


# ── Lock released on exception ─────────────────────────────────────────────────


class TestLockReleasedOnException:
    @pytest.mark.asyncio
    async def test_lock_released_after_do_send_raises(self) -> None:
        """_send_lock must be released even when _do_send_dps raises."""
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()

        async def failing_send(dps: dict[str, Any]) -> None:
            raise HomeAssistantError("simulated send failure")

        coord._do_send_dps = failing_send  # type: ignore[method-assign]

        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})

        # The lock must not be held after the exception
        assert not coord._send_lock.locked()

    @pytest.mark.asyncio
    async def test_subsequent_send_succeeds_after_exception(self) -> None:
        """After a failed send, the next send must acquire the lock normally."""
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        call_count = 0

        async def sometimes_failing_send(dps: dict[str, Any]) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HomeAssistantError("first call fails")

        coord._do_send_dps = sometimes_failing_send  # type: ignore[method-assign]

        # First send fails
        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})

        # Second send must succeed — the lock was released
        await coord.async_send_dps({"1": False})
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_lock_released_after_timeout(self) -> None:
        """Timeout during _do_send_dps must release _send_lock."""
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        coord._command_timeout = 0.01

        async def slow_send(dps: dict[str, Any]) -> None:
            await asyncio.sleep(10)

        coord._do_send_dps = slow_send  # type: ignore[method-assign]

        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})

        assert not coord._send_lock.locked()

    @pytest.mark.asyncio
    async def test_second_send_completes_after_first_times_out(self) -> None:
        """After a timed-out send the lock must be free for the next caller."""
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        coord._command_timeout = 0.01

        call_count = 0

        async def conditional_slow_send(dps: dict[str, Any]) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(10)  # Will be timed-out
            # Second call returns immediately

        coord._do_send_dps = conditional_slow_send  # type: ignore[method-assign]

        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})

        # The lock must have been released; this must not hang
        await coord.async_send_dps({"1": False})
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_sends_one_fails_others_succeed(self) -> None:
        """When one send raises, the remaining concurrent sends must still complete."""
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        completed: list[int] = []
        failed: list[int] = []

        async def partial_failure_send(dps: dict[str, Any]) -> None:
            idx = dps["idx"]
            if idx == 2:
                raise HomeAssistantError(f"send {idx} failed")
            await asyncio.sleep(0.005)
            completed.append(idx)

        coord._do_send_dps = partial_failure_send  # type: ignore[method-assign]

        results = await asyncio.gather(
            coord.async_send_dps({"idx": 1}),
            coord.async_send_dps({"idx": 2}),
            coord.async_send_dps({"idx": 3}),
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, HomeAssistantError):
                failed.append(1)

        # Exactly one failure and two successes
        assert len(failed) == 1
        assert len(completed) == 2
        # Lock must be free after all tasks settle
        assert not coord._send_lock.locked()


# ── Lock state properties ──────────────────────────────────────────────────────


class TestSendLockState:
    def test_lock_initially_unlocked(self) -> None:
        """_send_lock must start in unlocked state."""
        coord = _make_coordinator()
        assert not coord._send_lock.locked()

    @pytest.mark.asyncio
    async def test_lock_held_during_send(self) -> None:
        """_send_lock must be held while _do_send_dps is running."""
        coord = _make_coordinator()
        lock_was_held = False

        async def check_lock(dps: dict[str, Any]) -> None:
            nonlocal lock_was_held
            lock_was_held = coord._send_lock.locked()

        coord._do_send_dps = check_lock  # type: ignore[method-assign]

        await coord.async_send_dps({"1": True})

        assert lock_was_held is True
        # Lock released after send
        assert not coord._send_lock.locked()

    @pytest.mark.asyncio
    async def test_lock_not_held_when_device_unavailable(self) -> None:
        """When the device is unavailable the lock must not be acquired."""
        from homeassistant.exceptions import HomeAssistantError

        coord = _make_coordinator()
        coord.state.available = False

        with pytest.raises(HomeAssistantError):
            await coord.async_send_dps({"1": True})

        assert not coord._send_lock.locked()
