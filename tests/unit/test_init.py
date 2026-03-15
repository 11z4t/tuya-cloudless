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
        dev_info = MagicMock()
        runtime = TuyaCloudlessRuntimeData(
            coordinator=coord,
            device_info=dev_info,
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
            device_info=MagicMock(),
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

    def test_resolves_by_profile_name(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        profile = DeviceProfile(
            name="Smart Plug",
            model="sp*",
            entities=[],
        )

        with (
            patch(
                "custom_components.tuya_cloudless.find_profile",
                return_value=profile,
            ),
            patch("custom_components.tuya_cloudless._ensure_profiles"),
        ):
            entry = self._make_entry({"profile": "Smart Plug"})
            result = _resolve_profile(entry)
        assert result is not None
        assert result.name == "Smart Plug"

    def test_returns_none_when_no_profiles(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        with (
            patch(
                "custom_components.tuya_cloudless.find_profile",
                return_value=None,
            ),
            patch(
                "custom_components.tuya_cloudless.list_profiles",
                return_value=[],
            ),
            patch(
                "custom_components.tuya_cloudless._ensure_profiles",
            ),
        ):
            entry = self._make_entry({"profile": "Missing Profile"})
            result = _resolve_profile(entry)
        assert result is None

    def test_falls_back_to_first_profile(self) -> None:
        from custom_components.tuya_cloudless import _resolve_profile

        fallback = DeviceProfile(name="Generic Switch", model="*", entities=[])

        with (
            patch(
                "custom_components.tuya_cloudless.find_profile",
                return_value=None,
            ),
            patch(
                "custom_components.tuya_cloudless.list_profiles",
                return_value=[fallback],
            ),
            patch(
                "custom_components.tuya_cloudless._ensure_profiles",
            ),
        ):
            entry = self._make_entry({"profile": "Unknown"})
            result = _resolve_profile(entry)
        assert result is not None
        assert result.name == "Generic Switch"

    def test_resolves_by_legacy_device_type(self) -> None:
        """Entry uses old 'device_type' field — mapped via DEVICE_TYPE_TO_PROFILE."""
        from custom_components.tuya_cloudless import _resolve_profile

        profile = DeviceProfile(name="Generic Switch", model="*", entities=[])

        with (
            patch(
                "custom_components.tuya_cloudless.find_profile",
                return_value=profile,
            ),
            patch("custom_components.tuya_cloudless._ensure_profiles"),
        ):
            # Legacy entry: no "profile" key, uses "device_type"
            entry = self._make_entry({"device_type": "switch"})
            result = _resolve_profile(entry)

        assert result is not None
        assert result.name == "Generic Switch"


# ── _ensure_profiles ───────────────────────────────────────────────────────────


class TestEnsureProfiles:
    def test_ensure_profiles_calls_init_when_not_loaded(self) -> None:
        import custom_components.tuya_cloudless as init_mod

        original = init_mod._PROFILES_LOADED
        try:
            init_mod._PROFILES_LOADED = False
            with patch("custom_components.tuya_cloudless.init_profiles") as mock_init:
                init_mod._ensure_profiles()
            mock_init.assert_called_once()
        finally:
            init_mod._PROFILES_LOADED = original

    def test_ensure_profiles_idempotent_when_already_loaded(self) -> None:
        import custom_components.tuya_cloudless as init_mod

        original = init_mod._PROFILES_LOADED
        try:
            init_mod._PROFILES_LOADED = True
            with patch("custom_components.tuya_cloudless.init_profiles") as mock_init:
                init_mod._ensure_profiles()
            mock_init.assert_not_called()
        finally:
            init_mod._PROFILES_LOADED = original


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
    async def test_send_raw_dps_unexpected_exception_wrapped(self) -> None:
        """Non-HomeAssistantError exceptions are wrapped in HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.tuya_cloudless import _register_services

        hass = MagicMock()
        hass.services.has_service.return_value = False
        _register_services(hass)

        handler = hass.services.async_register.call_args[0][2]

        coord = MagicMock()
        coord.async_send_dps = AsyncMock(side_effect=RuntimeError("unexpected"))
        runtime = MagicMock()
        runtime.coordinator = coord
        entry = MagicMock()
        entry.runtime_data = runtime
        hass.config_entries.async_get_entry.return_value = entry

        call = MagicMock()
        call.data = {"entry_id": "abc", "dps": {"1": True}}

        with pytest.raises(HomeAssistantError):
            await handler(call)


# ── async_migrate_entry ─────────────────────────────────────────────────────────


class TestMigrateEntry:
    @pytest.mark.asyncio
    async def test_migrate_entry_version_too_new_returns_false(self) -> None:
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 2  # > 1
        result = await async_migrate_entry(MagicMock(), entry)
        assert result is False

    @pytest.mark.asyncio
    async def test_migrate_entry_version_1_returns_true(self) -> None:
        from custom_components.tuya_cloudless import async_migrate_entry

        entry = MagicMock()
        entry.version = 1
        result = await async_migrate_entry(MagicMock(), entry)
        assert result is True


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
