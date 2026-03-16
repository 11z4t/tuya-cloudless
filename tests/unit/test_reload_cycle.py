"""Tests for reload/unload integration cycle behaviour.

Verifies that setup→unload→setup cycles (HA reload) leave clean state:
no orphaned coordinator tasks, correct pairing server lifecycle,
and no resource leaks across multiple cycles.

PLAT-728
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.tuya_cloudless.const import DOMAIN

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_hass(num_entries: int = 1) -> MagicMock:
    """Return a minimal HomeAssistant mock.

    Args:
        num_entries: Number of config entries ``async_entries(DOMAIN)`` reports.
                     Controls whether the pairing server is stopped on unload.
    """
    hass: MagicMock = MagicMock()
    hass.data: dict[str, Any] = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    # Return a list of `num_entries` mock entries so the "remaining entries"
    # check in async_unload_entry behaves correctly.
    hass.config_entries.async_entries = MagicMock(return_value=[MagicMock()] * num_entries)
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kw: asyncio.ensure_future(coro))
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)  # Skip service registration
    return hass


def _make_entry(entry_id: str = "test_entry") -> MagicMock:
    """Return a minimal config entry mock.

    Args:
        entry_id: Unique entry ID — must differ between entries in multi-entry tests.
    """
    entry: MagicMock = MagicMock()
    entry.entry_id = entry_id
    entry.title = f"Test Device ({entry_id})"
    entry.data = {
        "gw_id": f"gw_{entry_id}",
        "local_key": "0123456789abcdef",
        "ip_address": "192.168.1.42",
        "protocol_version": "3.3",
        "profile": "Generic Switch",
        "device_name": f"Test Device ({entry_id})",
    }
    entry.options = {}
    entry.runtime_data = None
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=lambda: None)
    entry.version = 1
    return entry


def _mock_coordinator() -> MagicMock:
    """Return a coordinator mock that never opens a real TCP connection."""
    coord: MagicMock = MagicMock()
    coord.async_start = AsyncMock()
    coord.async_stop = AsyncMock()
    coord.device_name = ""
    coord.profile_name = ""
    return coord


def _mock_profile() -> MagicMock:
    """Return a minimal device profile mock."""
    profile: MagicMock = MagicMock()
    profile.name = "Generic Switch"
    profile.entities = []
    return profile


# ── TestReloadCycle ────────────────────────────────────────────────────────────


class TestReloadCycle:
    """Integration-style reload/unload cycle tests for the tuya_cloudless integration."""

    @pytest.mark.asyncio
    async def test_single_reload_clean_state(self) -> None:
        """Setup → unload → setup should produce clean, independent runtime_data each time."""
        from custom_components.tuya_cloudless import async_setup_entry, async_unload_entry

        hass = _make_hass(num_entries=1)
        entry = _make_entry("entry_a")

        coord1 = _mock_coordinator()
        coord2 = _mock_coordinator()
        coord_calls: list[MagicMock] = [coord1, coord2]

        with (
            patch(
                "custom_components.tuya_cloudless.TuyaCloudlessCoordinator.from_config_entry",
                side_effect=coord_calls,
            ),
            patch(
                "custom_components.tuya_cloudless._resolve_profile",
                return_value=_mock_profile(),
            ),
            patch(
                "custom_components.tuya_cloudless.ensure_pairing_server",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.tuya_cloudless.stop_pairing_server",
                new=AsyncMock(),
            ),
        ):
            # --- First setup ---
            result = await async_setup_entry(hass, entry)
            assert result is True, "First setup should succeed"
            coord1.async_start.assert_awaited_once()
            runtime_after_first_setup = entry.runtime_data
            assert runtime_after_first_setup is not None

            # --- Unload ---
            unload_result = await async_unload_entry(hass, entry)
            assert unload_result is True, "Unload should succeed"
            coord1.async_stop.assert_awaited_once()

            # --- Second setup (simulates reload) ---
            result = await async_setup_entry(hass, entry)
            assert result is True, "Second setup after reload should succeed"
            coord2.async_start.assert_awaited_once()

            # Fresh runtime_data from the second coordinator, not the first
            runtime_after_second_setup = entry.runtime_data
            assert runtime_after_second_setup is not None
            assert runtime_after_second_setup.coordinator is coord2
            assert runtime_after_first_setup.coordinator is coord1

    @pytest.mark.asyncio
    async def test_triple_reload_no_orphaned_tasks(self) -> None:
        """Three consecutive setup/unload cycles must stop each coordinator cleanly."""
        from custom_components.tuya_cloudless import async_setup_entry, async_unload_entry

        hass = _make_hass(num_entries=1)
        entry = _make_entry("entry_b")

        coordinators = [_mock_coordinator() for _ in range(3)]

        with (
            patch(
                "custom_components.tuya_cloudless.TuyaCloudlessCoordinator.from_config_entry",
                side_effect=coordinators,
            ),
            patch(
                "custom_components.tuya_cloudless._resolve_profile",
                return_value=_mock_profile(),
            ),
            patch(
                "custom_components.tuya_cloudless.ensure_pairing_server",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.tuya_cloudless.stop_pairing_server",
                new=AsyncMock(),
            ),
        ):
            for i, coord in enumerate(coordinators):
                result = await async_setup_entry(hass, entry)
                assert result is True, f"Setup cycle {i + 1} should succeed"
                coord.async_start.assert_awaited_once()

                unload_result = await async_unload_entry(hass, entry)
                assert unload_result is True, f"Unload cycle {i + 1} should succeed"
                coord.async_stop.assert_awaited_once()

        # Every coordinator was started and stopped exactly once — no orphans
        for i, coord in enumerate(coordinators):
            assert coord.async_start.await_count == 1, (
                f"Coordinator {i} async_start called unexpected number of times"
            )
            assert coord.async_stop.await_count == 1, (
                f"Coordinator {i} async_stop called unexpected number of times"
            )

    @pytest.mark.asyncio
    async def test_pairing_server_survives_reload(self) -> None:
        """Pairing server must remain running across a single-entry reload cycle."""
        from custom_components.tuya_cloudless import async_setup_entry, async_unload_entry
        from custom_components.tuya_cloudless.pairing_server import (
            _KEY_PAIRING_SERVER,
            PairingServer,
        )

        hass = _make_hass(num_entries=1)
        entry = _make_entry("entry_c")

        # Install a real PairingServer mock into hass.data so the integration
        # can check its presence via get_pairing_server / ensure_pairing_server.
        mock_server: MagicMock = MagicMock(spec=PairingServer)
        mock_server.start = AsyncMock()
        mock_server.stop = AsyncMock()
        hass.data.setdefault(DOMAIN, {})[_KEY_PAIRING_SERVER] = mock_server

        with (
            patch(
                "custom_components.tuya_cloudless.TuyaCloudlessCoordinator.from_config_entry",
                side_effect=[_mock_coordinator(), _mock_coordinator()],
            ),
            patch(
                "custom_components.tuya_cloudless._resolve_profile",
                return_value=_mock_profile(),
            ),
            # Pairing server is "already running" — ensure_pairing_server returns the mock
            patch(
                "custom_components.tuya_cloudless.ensure_pairing_server",
                return_value=mock_server,
            ) as mock_ensure,
            patch(
                "custom_components.tuya_cloudless.stop_pairing_server",
                new=AsyncMock(),
            ) as mock_stop,
        ):
            # First setup
            await async_setup_entry(hass, entry)
            mock_ensure.assert_awaited_once()

            # Unload (num_entries=1 so remaining≤1 → stop_pairing_server is called)
            await async_unload_entry(hass, entry)
            mock_stop.assert_awaited_once()

            # Second setup — ensure_pairing_server must be called again
            await async_setup_entry(hass, entry)
            assert mock_ensure.await_count == 2, (
                "ensure_pairing_server should be called on every setup"
            )

    @pytest.mark.asyncio
    async def test_pairing_server_stops_on_last_unload(self) -> None:
        """Pairing server must stop only when the last config entry is unloaded."""
        from custom_components.tuya_cloudless import async_setup_entry, async_unload_entry

        # Two entries are active — async_entries initially returns 2 items.
        # After the first unload we simulate one entry remaining (still > 1 → don't stop).
        # After the second unload only one is left (≤ 1 → stop).
        hass = _make_hass(num_entries=2)
        entry_a = _make_entry("entry_d1")
        entry_b = _make_entry("entry_d2")

        stop_server = AsyncMock()

        with (
            patch(
                "custom_components.tuya_cloudless.TuyaCloudlessCoordinator.from_config_entry",
                side_effect=[
                    _mock_coordinator(),
                    _mock_coordinator(),
                ],
            ),
            patch(
                "custom_components.tuya_cloudless._resolve_profile",
                return_value=_mock_profile(),
            ),
            patch(
                "custom_components.tuya_cloudless.ensure_pairing_server",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.tuya_cloudless.stop_pairing_server",
                new=stop_server,
            ),
        ):
            # Set up both entries
            await async_setup_entry(hass, entry_a)
            await async_setup_entry(hass, entry_b)

            # Unload first entry — still 2 entries reported by async_entries,
            # so len(remaining) > 1 → stop_pairing_server should NOT be called.
            await async_unload_entry(hass, entry_a)
            stop_server.assert_not_awaited()

            # Now simulate only one entry remaining
            hass.config_entries.async_entries = MagicMock(return_value=[MagicMock()])

            # Unload second entry — len(remaining) <= 1 → stop IS called.
            await async_unload_entry(hass, entry_b)
            stop_server.assert_awaited_once()
