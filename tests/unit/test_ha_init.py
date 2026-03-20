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

    def _make_hass_with_registry(self, registry: MagicMock) -> MagicMock:
        """Return a hass mock pre-populated with a mock ProfileRegistry."""
        from custom_components.tuya_cloudless import _KEY_PROFILE_REGISTRY
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = self._make_hass_with_data()
        hass.data = {DOMAIN: {_KEY_PROFILE_REGISTRY: registry}}
        return hass

    async def test_profile_from_entry_data(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        mock_profile = MagicMock()
        mock_profile.name = "Test Profile"
        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = mock_profile

        hass = self._make_hass_with_registry(mock_registry)
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "profile": "Test Profile",
            }
        )

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
            result = await _resolve_profile(hass, entry)
        mock_registry.find_profile.assert_called_once_with("Test Profile")
        assert result == mock_profile

    async def test_legacy_device_type_mapping(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        mock_profile = MagicMock()
        mock_profile.name = "Smart Plug"
        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = mock_profile

        hass = self._make_hass_with_registry(mock_registry)
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "device_type": "plug",
            }
        )

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
            await _resolve_profile(hass, entry)
        mock_registry.find_profile.assert_called_once_with("Smart Plug")

    async def test_fallback_when_profile_not_found(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        fallback = MagicMock()
        fallback.name = "Fallback"
        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = None
        mock_registry.list_profiles.return_value = [fallback]

        hass = self._make_hass_with_registry(mock_registry)
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "profile": "NonExistent",
            }
        )

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
            result = await _resolve_profile(hass, entry)
        assert result == fallback

    async def test_returns_none_when_no_profiles(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = None
        mock_registry.list_profiles.return_value = []

        hass = self._make_hass_with_registry(mock_registry)
        entry = _make_entry(
            data={
                "gw_id": "abc",
                "local_key": "0123456789abcdef",
                "ip_address": "1.2.3.4",
                "profile": "NonExistent",
            }
        )

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
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
        from custom_components.tuya_cloudless import _KEY_PROFILE_REGISTRY, _ensure_profiles
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = MagicMock()
        hass.data = {DOMAIN: {_KEY_PROFILE_REGISTRY: MagicMock()}}
        hass.async_add_executor_job = AsyncMock()

        await _ensure_profiles(hass)
        hass.async_add_executor_job.assert_not_called()


# ── ProfileRegistry isolation (AC3 — PLAT-770) ────────────────────────────────


class TestProfileRegistryIsolation:
    """AC3: two hass instances must receive separate ProfileRegistry objects."""

    @pytest.mark.asyncio
    async def test_two_hass_instances_have_separate_registries(self, tmp_path: Any) -> None:
        from tuya_cloudless.profiles import ProfileRegistry

        from custom_components.tuya_cloudless import (
            _KEY_PROFILE_REGISTRY,
            _ensure_profiles,
        )
        from custom_components.tuya_cloudless.const import DOMAIN

        async def _fake_executor(fn, *args):  # type: ignore[no-untyped-def]
            fn(*args)

        hass_a = MagicMock()
        hass_a.data = {}
        hass_a.async_add_executor_job = AsyncMock(side_effect=_fake_executor)

        hass_b = MagicMock()
        hass_b.data = {}
        hass_b.async_add_executor_job = AsyncMock(side_effect=_fake_executor)

        with patch("custom_components.tuya_cloudless.PROFILES_DIR", tmp_path):
            await _ensure_profiles(hass_a)
            await _ensure_profiles(hass_b)

        registry_a = hass_a.data[DOMAIN][_KEY_PROFILE_REGISTRY]
        registry_b = hass_b.data[DOMAIN][_KEY_PROFILE_REGISTRY]

        assert isinstance(registry_a, ProfileRegistry)
        assert isinstance(registry_b, ProfileRegistry)
        assert registry_a is not registry_b, "Each hass instance must have its own registry"

    @pytest.mark.asyncio
    async def test_second_call_reuses_same_registry(self, tmp_path: Any) -> None:
        from custom_components.tuya_cloudless import (
            _KEY_PROFILE_REGISTRY,
            _ensure_profiles,
        )
        from custom_components.tuya_cloudless.const import DOMAIN

        async def _fake_executor(fn, *args):  # type: ignore[no-untyped-def]
            fn(*args)

        hass = MagicMock()
        hass.data = {}
        hass.async_add_executor_job = AsyncMock(side_effect=_fake_executor)

        with patch("custom_components.tuya_cloudless.PROFILES_DIR", tmp_path):
            await _ensure_profiles(hass)
            registry_first = hass.data[DOMAIN][_KEY_PROFILE_REGISTRY]
            await _ensure_profiles(hass)
            registry_second = hass.data[DOMAIN][_KEY_PROFILE_REGISTRY]

        assert registry_first is registry_second, "Same registry must be reused for same hass"
        # Executor called exactly once (idempotent)
        hass.async_add_executor_job.assert_called_once()


# ── _async_update_listener ────────────────────────────────────────────────────


class TestUpdateListener:
    @pytest.mark.asyncio
    async def test_reloads_entry(self) -> None:
        from custom_components.tuya_cloudless import _async_update_listener

        hass = _make_hass()
        entry = _make_entry()
        await _async_update_listener(hass, entry)
        hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


# ── send_raw_dps schema validation (ROB-004 / PLAT-833) ────────────────────────


class TestSendRawDpsSchema:
    """Verify the voluptuous schema on send_raw_dps rejects invalid input."""

    def test_schema_accepts_valid_dps(self) -> None:

        from custom_components.tuya_cloudless import _SEND_RAW_DPS_SCHEMA

        valid = {"entry_id": "abc123", "dps": {"1": True, "2": 100, "3": "on"}}
        result = _SEND_RAW_DPS_SCHEMA(valid)
        assert result["entry_id"] == "abc123"
        assert result["dps"] == {"1": True, "2": 100, "3": "on"}

    def test_schema_rejects_missing_entry_id(self) -> None:
        import voluptuous as vol

        from custom_components.tuya_cloudless import _SEND_RAW_DPS_SCHEMA

        with pytest.raises(vol.Invalid):
            _SEND_RAW_DPS_SCHEMA({"dps": {"1": True}})

    def test_schema_rejects_missing_dps(self) -> None:
        import voluptuous as vol

        from custom_components.tuya_cloudless import _SEND_RAW_DPS_SCHEMA

        with pytest.raises(vol.Invalid):
            _SEND_RAW_DPS_SCHEMA({"entry_id": "abc"})

    def test_schema_rejects_invalid_dps_value_type(self) -> None:
        """DPS values must be bool, int, or str — floats and lists are rejected."""
        import voluptuous as vol

        from custom_components.tuya_cloudless import _SEND_RAW_DPS_SCHEMA

        with pytest.raises(vol.Invalid):
            _SEND_RAW_DPS_SCHEMA({"entry_id": "abc", "dps": {"1": 3.14}})

    def test_schema_rejects_list_as_dps_value(self) -> None:
        import voluptuous as vol

        from custom_components.tuya_cloudless import _SEND_RAW_DPS_SCHEMA

        with pytest.raises(vol.Invalid):
            _SEND_RAW_DPS_SCHEMA({"entry_id": "abc", "dps": {"1": [1, 2, 3]}})

    def test_schema_rejects_non_string_dps_key(self) -> None:
        """DPS keys must be strings."""
        import voluptuous as vol

        from custom_components.tuya_cloudless import _SEND_RAW_DPS_SCHEMA

        with pytest.raises((vol.Invalid, TypeError)):
            _SEND_RAW_DPS_SCHEMA({"entry_id": "abc", "dps": {1: True}})

    def test_schema_exported_from_module(self) -> None:
        """_SEND_RAW_DPS_SCHEMA must be importable from the init module."""
        import custom_components.tuya_cloudless as init_mod

        assert hasattr(init_mod, "_SEND_RAW_DPS_SCHEMA")


# ── _handle_send_raw_dps HomeAssistantError re-raise (line 270) ───────────────


class TestSendRawDpsHandlerHomeAssistantError:
    """Verify that HomeAssistantError from coordinator propagates as-is (line 268-270)."""

    @pytest.mark.asyncio
    async def test_coordinator_homeassistant_error_propagates(self) -> None:
        """If coordinator.async_send_dps raises HomeAssistantError, it must propagate.

        _handle_send_raw_dps is a closure created by _register_services, so we
        trigger it by calling async_setup_entry (which calls _register_services)
        and then invoking the registered service handler directly.
        """
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData, async_setup_entry

        hass = _make_hass()
        entry = _make_entry()

        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.async_send_dps = AsyncMock(
            side_effect=HomeAssistantError("device rejected command")
        )

        mock_profile = MagicMock()
        mock_profile.name = "Generic Switch"
        mock_profile.entities = []

        # Capture the registered service handler
        registered_handlers: dict[str, object] = {}

        def _capture_register(domain: str, service: str, handler: object, **kw: object) -> None:
            registered_handlers[service] = handler

        hass.services.has_service = MagicMock(return_value=False)
        hass.services.async_register = MagicMock(side_effect=_capture_register)

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
            await async_setup_entry(hass, entry)

        handler = registered_handlers.get("send_raw_dps")
        assert handler is not None

        # Set up a runtime_data so the handler can find the coordinator
        entry.runtime_data = TuyaCloudlessRuntimeData(
            coordinator=mock_coord,
            entity_specs=(),
            profile_name="Generic Switch",
        )

        # hass.config_entries.async_get_entry must return our entry
        hass.config_entries.async_get_entry = MagicMock(return_value=entry)

        call = MagicMock()
        call.data = {"entry_id": entry.entry_id, "dps": {"1": True}}

        with pytest.raises(HomeAssistantError):
            await handler(call)  # type: ignore[operator]


# ── _safe_device_url invalid IP (lines 83-84) ─────────────────────────────────


class TestSafeDeviceUrlInvalidInput:
    """Lines 83-84: _safe_device_url returns None for non-IP strings."""

    def test_invalid_ip_returns_none(self) -> None:
        """A hostname (not an IP literal) must return None."""
        from custom_components.tuya_cloudless import _safe_device_url

        assert _safe_device_url("not-an-ip.example.com") is None

    def test_empty_string_returns_none(self) -> None:
        """An empty string must return None."""
        from custom_components.tuya_cloudless import _safe_device_url

        assert _safe_device_url("") is None
