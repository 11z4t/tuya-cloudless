"""Tests for custom_components.tuya_cloudless.__init__ (setup/unload)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kw: asyncio.ensure_future(coro))
    return hass


def _make_entry(
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    title: str = "Test Device",
) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = title
    entry.data = data or {
        "gw_id": "abc123",
        "local_key": "0123456789abcdef",
        "ip_address": "192.168.1.42",
        "protocol_version": "3.3",
        "profile": "Generic Switch",
        "device_name": "Test Device",
    }
    entry.options = options or {}
    entry.runtime_data = None
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=lambda: None)
    entry.version = 1
    return entry


# ── _resolve_profile ──────────────────────────────────────────────────────────


class TestResolveProfile:
    def _make_hass_with_data(self) -> MagicMock:
        hass = _make_hass()
        hass.data = {}
        hass.async_add_executor_job = AsyncMock()
        return hass

    async def test_profile_from_entry_data(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        hass = self._make_hass_with_data()
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "profile": "Test Profile",
            }
        )

        mock_profile = MagicMock()
        mock_profile.name = "Test Profile"
        with (
            patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()),
            patch(
                "custom_components.tuya_cloudless.find_profile", return_value=mock_profile
            ) as mock_find,
        ):
            result = await _resolve_profile(hass, entry)
        mock_find.assert_called_once_with("Test Profile")
        assert result == mock_profile

    async def test_legacy_device_type_mapping(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        hass = self._make_hass_with_data()
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "device_type": "plug",
            }
        )

        mock_profile = MagicMock()
        mock_profile.name = "Smart Plug"
        with (
            patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()),
            patch(
                "custom_components.tuya_cloudless.find_profile", return_value=mock_profile
            ) as mock_find,
        ):
            await _resolve_profile(hass, entry)
        mock_find.assert_called_once_with("Smart Plug")

    async def test_fallback_when_profile_not_found(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        hass = self._make_hass_with_data()
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "profile": "NonExistent",
            }
        )

        fallback = MagicMock()
        fallback.name = "Fallback"
        with (
            patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()),
            patch("custom_components.tuya_cloudless.find_profile", return_value=None),
            patch("custom_components.tuya_cloudless.list_profiles", return_value=[fallback]),
        ):
            result = await _resolve_profile(hass, entry)
        assert result == fallback

    async def test_returns_none_when_no_profiles(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        hass = self._make_hass_with_data()
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "profile": "NonExistent",
            }
        )

        with (
            patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()),
            patch("custom_components.tuya_cloudless.find_profile", return_value=None),
            patch("custom_components.tuya_cloudless.list_profiles", return_value=[]),
        ):
            result = await _resolve_profile(hass, entry)
        assert result is None


# ── TuyaCloudlessRuntimeData ──────────────────────────────────────────────────


class TestRuntimeData:
    def test_dataclass_fields(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData

        rt = TuyaCloudlessRuntimeData(
            coordinator=MagicMock(),
            entity_specs=(),
            profile_name="Test",
        )
        assert rt.entity_specs == ()
        assert rt.profile_name == "Test"

    def test_default_entity_specs(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData

        rt = TuyaCloudlessRuntimeData(
            coordinator=MagicMock(),
            entity_specs=(),
            profile_name="",
        )
        assert rt.entity_specs == ()
        assert rt.profile_name == ""


# ── async_setup_entry ─────────────────────────────────────────────────────────


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_entry_success(self) -> None:
        from custom_components.tuya_cloudless import async_setup_entry

        hass = _make_hass()
        entry = _make_entry()

        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()

        mock_profile = MagicMock()
        mock_profile.name = "Generic Switch"
        mock_profile.entities = []

        with (
            patch(
                "custom_components.tuya_cloudless.TuyaCloudlessCoordinator.from_config_entry",
                return_value=mock_coord,
            ),
            patch(
                "custom_components.tuya_cloudless._resolve_profile",
                return_value=mock_profile,
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        mock_coord.async_start.assert_awaited_once()
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_setup_entry_no_profile(self) -> None:
        from custom_components.tuya_cloudless import async_setup_entry

        hass = _make_hass()
        entry = _make_entry()

        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()

        with (
            patch(
                "custom_components.tuya_cloudless.TuyaCloudlessCoordinator.from_config_entry",
                return_value=mock_coord,
            ),
            patch(
                "custom_components.tuya_cloudless._resolve_profile",
                return_value=None,
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        # Runtime data should have empty specs
        assert entry.runtime_data.entity_specs == ()
        assert entry.runtime_data.profile_name == ""


# ── async_unload_entry ────────────────────────────────────────────────────────


class TestAsyncUnloadEntry:
    @pytest.mark.asyncio
    async def test_unload_entry(self) -> None:
        from custom_components.tuya_cloudless import (
            TuyaCloudlessRuntimeData,
            async_unload_entry,
        )

        hass = _make_hass()
        entry = _make_entry()
        mock_coord = MagicMock()
        mock_coord.async_stop = AsyncMock()
        entry.runtime_data = TuyaCloudlessRuntimeData(
            coordinator=mock_coord,
            entity_specs=(),
            profile_name="",
        )

        result = await async_unload_entry(hass, entry)
        assert result is True
        mock_coord.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unload_entry_no_runtime_data(self) -> None:
        from custom_components.tuya_cloudless import async_unload_entry

        hass = _make_hass()
        entry = _make_entry()
        entry.runtime_data = None

        result = await async_unload_entry(hass, entry)
        assert result is True


# ── async_migrate_entry ───────────────────────────────────────────────────────


class TestAsyncMigrateEntry:
    @pytest.mark.asyncio
    async def test_migrate_v1(self) -> None:
        from custom_components.tuya_cloudless import async_migrate_entry

        hass = _make_hass()
        entry = _make_entry()
        entry.version = 1
        result = await async_migrate_entry(hass, entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_migrate_unknown_version(self) -> None:
        from custom_components.tuya_cloudless import async_migrate_entry

        hass = _make_hass()
        entry = _make_entry()
        entry.version = 99
        result = await async_migrate_entry(hass, entry)
        assert result is False


# ── _ensure_profiles ──────────────────────────────────────────────────────────


class TestEnsureProfiles:
    async def test_loads_only_once(self) -> None:
        from custom_components.tuya_cloudless import _ensure_profiles

        hass = MagicMock()
        hass.data = {}
        hass.async_add_executor_job = AsyncMock()

        await _ensure_profiles(hass)
        await _ensure_profiles(hass)  # second call should be skipped

        # init_profiles is passed to executor job — only once
        hass.async_add_executor_job.assert_called_once()

    async def test_skips_when_already_loaded(self) -> None:
        from custom_components.tuya_cloudless import _KEY_PROFILES_LOADED, _ensure_profiles
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = MagicMock()
        hass.data = {DOMAIN: {_KEY_PROFILES_LOADED: True}}
        hass.async_add_executor_job = AsyncMock()

        await _ensure_profiles(hass)
        hass.async_add_executor_job.assert_not_called()


# ── _async_update_listener ────────────────────────────────────────────────────


class TestUpdateListener:
    @pytest.mark.asyncio
    async def test_reloads_entry(self) -> None:
        from custom_components.tuya_cloudless import _async_update_listener

        hass = _make_hass()
        entry = _make_entry()
        await _async_update_listener(hass, entry)
        hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
