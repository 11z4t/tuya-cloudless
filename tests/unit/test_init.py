"""Unit tests for custom_components.tuya_cloudless.__init__ module.

Focuses on profile resolution, RuntimeData, and service handlers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from tuya_cloudless.profiles import DeviceProfile, DPSpec, EntitySpec  # noqa: E402

# ── TuyaCloudlessRuntimeData ───────────────────────────────────────────────────


class TestRuntimeData:
    def test_frozen_dataclass_prevents_mutation(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData

        coord = MagicMock()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            entity_specs=(),
            profile_name="Test",
        )
        with pytest.raises((AttributeError, TypeError)):
            runtime.profile_name = "Changed"  # type: ignore[misc]

    def test_entity_specs_is_tuple(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessRuntimeData

        spec = EntitySpec(platform="switch", name="s", dp_power=DPSpec(id="1", type="bool"))
        runtime = TuyaCloudlessRuntimeData(
            coordinator=MagicMock(),
            entity_specs=(spec,),
            profile_name="Test",
        )
        assert isinstance(runtime.entity_specs, tuple)
        assert len(runtime.entity_specs) == 1


# ── TypeAlias ─────────────────────────────────────────────────────────────────


class TestConfigEntryAlias:
    def test_alias_is_importable(self) -> None:
        from custom_components.tuya_cloudless import TuyaCloudlessConfigEntry

        assert TuyaCloudlessConfigEntry is not None


# ── Profile resolution ─────────────────────────────────────────────────────────


class TestResolveProfile:
    def _make_entry(self, data: dict) -> MagicMock:
        entry = MagicMock()
        entry.data = data
        entry.entry_id = "test_entry"
        return entry

    def _make_hass(self, registry: MagicMock | None = None) -> MagicMock:
        from custom_components.tuya_cloudless import _KEY_PROFILE_REGISTRY
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = MagicMock()
        reg = registry if registry is not None else MagicMock()
        hass.data = {DOMAIN: {_KEY_PROFILE_REGISTRY: reg}}
        hass.async_add_executor_job = AsyncMock()
        return hass

    async def test_resolves_by_profile_name(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        profile = DeviceProfile(name="Smart Plug", model="sp*", entities=[])
        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = profile

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
            entry = self._make_entry({"profile": "Smart Plug"})
            result = await _resolve_profile(self._make_hass(mock_registry), entry)
        assert result is not None
        assert result.name == "Smart Plug"

    async def test_returns_none_when_no_profiles(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = None
        mock_registry.list_profiles.return_value = []

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
            entry = self._make_entry({"profile": "Missing Profile"})
            result = await _resolve_profile(self._make_hass(mock_registry), entry)
        assert result is None

    async def test_falls_back_to_first_profile(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        fallback = DeviceProfile(name="Generic Switch", model="*", entities=[])
        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = None
        mock_registry.list_profiles.return_value = [fallback]

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
            entry = self._make_entry({"profile": "Unknown"})
            result = await _resolve_profile(self._make_hass(mock_registry), entry)
        assert result is not None
        assert result.name == "Generic Switch"

    async def test_resolves_by_legacy_device_type(self) -> None:
        """Entry uses old 'device_type' field — mapped via DEVICE_TYPE_TO_PROFILE."""
        from custom_components.tuya_cloudless import _resolve_profile

        profile = DeviceProfile(name="Generic Switch", model="*", entities=[])
        mock_registry = MagicMock()
        mock_registry.find_profile.return_value = profile

        with patch("custom_components.tuya_cloudless._ensure_profiles", new=AsyncMock()):
            # Legacy entry: no "profile" key, uses "device_type"
            entry = self._make_entry({"device_type": "switch"})
            result = await _resolve_profile(self._make_hass(mock_registry), entry)

        assert result is not None
        assert result.name == "Generic Switch"


# ── _ensure_profiles ───────────────────────────────────────────────────────────


class TestEnsureProfiles:
    async def test_ensure_profiles_calls_init_when_not_loaded(self) -> None:
        from custom_components.tuya_cloudless import _ensure_profiles

        hass = MagicMock()
        hass.data = {}
        hass.async_add_executor_job = AsyncMock()

        await _ensure_profiles(hass)

        # init_profiles is dispatched to executor — verify it was called once
        hass.async_add_executor_job.assert_called_once()

    async def test_ensure_profiles_idempotent_when_already_loaded(self) -> None:
        from custom_components.tuya_cloudless import _KEY_PROFILE_REGISTRY, _ensure_profiles
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = MagicMock()
        hass.data = {DOMAIN: {_KEY_PROFILE_REGISTRY: MagicMock()}}
        hass.async_add_executor_job = AsyncMock()

        await _ensure_profiles(hass)
        hass.async_add_executor_job.assert_not_called()


# ── async_remove_config_entry_device ──────────────────────────────────────────


class TestRemoveDevice:
    @pytest.mark.asyncio
    async def test_always_returns_true(self) -> None:
        from custom_components.tuya_cloudless import async_remove_config_entry_device

        result = await async_remove_config_entry_device(MagicMock(), MagicMock(), MagicMock())
        assert result is True


# ── _register_services ────────────────────────────────────────────────────────


class TestRegisterServices:
    def test_services_registered_idempotent(self) -> None:
        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = True
        _register_services(hass)
        hass.services.async_register.assert_not_called()

    def test_services_registered_when_missing(self) -> None:
        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = False
        _register_services(hass)
        hass.services.async_register.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_raw_dps_entry_not_found(self) -> None:
        """ServiceValidationError raised when entry_id not in hass."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = False
        _register_services(hass)

        handler = hass.services.async_register.call_args[0][2]

        hass.config_entries.async_get_entry.return_value = None
        call = MagicMock()
        call.data = {"entry_id": "nonexistent", "dps": {"1": True}}

        with pytest.raises(ServiceValidationError):
            await handler(call)

    @pytest.mark.asyncio
    async def test_send_raw_dps_entry_not_loaded(self) -> None:
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = False
        _register_services(hass)

        handler = hass.services.async_register.call_args[0][2]

        entry = MagicMock(spec=[])  # no runtime_data attribute
        hass.config_entries.async_get_entry.return_value = entry
        call = MagicMock()
        call.data = {"entry_id": "abc", "dps": {"1": True}}

        with pytest.raises(ServiceValidationError):
            await handler(call)

    @pytest.mark.asyncio
    async def test_send_raw_dps_success(self) -> None:
        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = False
        _register_services(hass)

        handler = hass.services.async_register.call_args[0][2]

        coord = MagicMock()
        coord.async_send_dps = AsyncMock()
        runtime = MagicMock()
        runtime.coordinator = coord
        entry = MagicMock()
        entry.runtime_data = runtime
        hass.config_entries.async_get_entry.return_value = entry

        call = MagicMock()
        call.data = {"entry_id": "abc", "dps": {"1": True}}

        await handler(call)
        coord.async_send_dps.assert_called_once_with({"1": True})

    @pytest.mark.asyncio
    async def test_send_raw_dps_os_error_wrapped(self) -> None:
        """OSError from coordinator is wrapped in HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = False
        _register_services(hass)

        handler = hass.services.async_register.call_args[0][2]

        coord = MagicMock()
        coord.async_send_dps = AsyncMock(side_effect=OSError("network down"))
        runtime = MagicMock()
        runtime.coordinator = coord
        entry = MagicMock()
        entry.runtime_data = runtime
        hass.config_entries.async_get_entry.return_value = entry

        call = MagicMock()
        call.data = {"entry_id": "abc", "dps": {"1": True}}

        with pytest.raises(HomeAssistantError):
            await handler(call)

    @pytest.mark.asyncio
    async def test_send_raw_dps_unexpected_exception_propagates(self) -> None:
        """RuntimeError (programming bug) propagates uncaught — not wrapped."""
        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = False
        _register_services(hass)

        handler = hass.services.async_register.call_args[0][2]

        coord = MagicMock()
        coord.async_send_dps = AsyncMock(side_effect=RuntimeError("bug"))
        runtime = MagicMock()
        runtime.coordinator = coord
        entry = MagicMock()
        entry.runtime_data = runtime
        hass.config_entries.async_get_entry.return_value = entry

        call = MagicMock()
        call.data = {"entry_id": "abc", "dps": {"1": True}}

        with pytest.raises(RuntimeError):
            await handler(call)


# ── async_migrate_entry ─────────────────────────────────────────────────────────


class TestMigrateEntry:
    @pytest.mark.asyncio
    async def test_migrate_entry_version_too_new_returns_false(self) -> None:
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 3  # > 2 (current max)
        result = await async_migrate_entry(MagicMock(), entry)
        assert result is False

    @pytest.mark.asyncio
    async def test_migrate_entry_version_2_returns_true(self) -> None:
        """Version 2 is the current version — migration is a no-op."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 2
        hass = MagicMock()
        result = await async_migrate_entry(hass, entry)
        assert result is True
        hass.config_entries.async_update_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_migrate_entry_version_1_upgrades_to_2(self) -> None:
        """v1 → v2 moves ip_address/protocol_version from options to data."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 1
        entry.options = {
            "ip_address": "10.0.0.1",
            "protocol_version": "3.4",
            "heartbeat_interval": 30,
        }
        entry.data = {"gw_id": "abc", "local_key": "0123456789abcdef"}

        hass = MagicMock()
        result = await async_migrate_entry(hass, entry)
        assert result is True
        hass.config_entries.async_update_entry.assert_called_once()
        call_kwargs = hass.config_entries.async_update_entry.call_args[1]
        assert call_kwargs["version"] == 2
        assert call_kwargs["data"]["ip_address"] == "10.0.0.1"
        assert call_kwargs["data"]["protocol_version"] == "3.4"
        assert "heartbeat_interval" in call_kwargs["options"]
        assert "ip_address" not in call_kwargs["options"]
        assert "protocol_version" not in call_kwargs["options"]

    @pytest.mark.asyncio
    async def test_migrate_entry_version_1_no_stray_options(self) -> None:
        """v1 → v2: version bumped even when options contain no strays."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 1
        entry.options = {"heartbeat_interval": 20}
        entry.data = {"gw_id": "abc", "ip_address": "1.2.3.4"}

        hass = MagicMock()
        result = await async_migrate_entry(hass, entry)
        assert result is True
        hass.config_entries.async_update_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_migrate_v1_empty_options(self) -> None:
        """v1 with completely empty options dict still upgrades to v2."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 1
        entry.options = {}
        entry.data = {"gw_id": "dev1"}

        hass = MagicMock()
        result = await async_migrate_entry(hass, entry)
        assert result is True
        call_kwargs = hass.config_entries.async_update_entry.call_args[1]
        assert call_kwargs["version"] == 2
        assert call_kwargs["options"] == {}

    @pytest.mark.asyncio
    async def test_migrate_v1_data_preserved(self) -> None:
        """Existing data fields are preserved during v1 → v2 migration."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 1
        entry.options = {"ip_address": "192.168.1.5", "protocol_version": "3.5"}
        entry.data = {"gw_id": "abc", "local_key": "key16byteslong!"}

        hass = MagicMock()
        result = await async_migrate_entry(hass, entry)
        assert result is True
        call_kwargs = hass.config_entries.async_update_entry.call_args[1]
        new_data = call_kwargs["data"]
        # Original data fields remain
        assert new_data["gw_id"] == "abc"
        assert new_data["local_key"] == "key16byteslong!"
        # Migrated fields moved in
        assert new_data["ip_address"] == "192.168.1.5"
        assert new_data["protocol_version"] == "3.5"

    @pytest.mark.asyncio
    async def test_migrate_v1_options_only_removes_migrated_keys(self) -> None:
        """v1 → v2 removes ip_address/protocol_version from options, keeps rest."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 1
        entry.options = {
            "ip_address": "10.0.0.5",
            "protocol_version": "3.3",
            "command_timeout": 10.0,
            "reconnect_max_delay": 600,
        }
        entry.data = {"gw_id": "gw99"}

        hass = MagicMock()
        result = await async_migrate_entry(hass, entry)
        assert result is True
        call_kwargs = hass.config_entries.async_update_entry.call_args[1]
        new_opts = call_kwargs["options"]
        assert "ip_address" not in new_opts
        assert "protocol_version" not in new_opts
        assert new_opts["command_timeout"] == 10.0
        assert new_opts["reconnect_max_delay"] == 600

    @pytest.mark.asyncio
    async def test_migrate_future_version_returns_false(self) -> None:
        """Version 99 (far future) returns False — integration cannot downgrade."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 99
        result = await async_migrate_entry(MagicMock(), entry)
        assert result is False

    @pytest.mark.asyncio
    async def test_migrate_v1_result_is_idempotent_on_second_call(self) -> None:
        """Calling migrate twice on an already-v2 entry is a no-op."""
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 2
        entry.data = {"gw_id": "abc", "ip_address": "1.2.3.4", "protocol_version": "3.3"}
        entry.options = {}

        hass = MagicMock()
        result = await async_migrate_entry(hass, entry)
        assert result is True
        # Already at v2 — no update should be triggered
        hass.config_entries.async_update_entry.assert_not_called()


# ── async_unload_entry ──────────────────────────────────────────────────────────


class TestUnloadEntry:
    @pytest.mark.asyncio
    async def test_unload_calls_coordinator_stop(self) -> None:
        from custom_components.tuya_cloudless import async_unload_entry

        coord = MagicMock()
        coord.async_stop = AsyncMock()
        runtime = MagicMock()
        runtime.coordinator = coord
        entry = MagicMock()
        entry.runtime_data = runtime
        entry.title = "Test Device"

        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await async_unload_entry(hass, entry)

        coord.async_stop.assert_called_once()
        assert result is True

    @pytest.mark.asyncio
    async def test_unload_without_runtime_data(self) -> None:
        from custom_components.tuya_cloudless import async_unload_entry

        entry = MagicMock(spec=[])  # no runtime_data
        entry.title = "Test"

        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await async_unload_entry(hass, entry)
        assert result is True
