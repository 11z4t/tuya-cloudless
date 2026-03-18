"""Tests for custom_components.tuya_cloudless.update."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.tuya_cloudless.coordinator import DeviceState
from custom_components.tuya_cloudless.update import TuyaCloudlessUpdateEntity

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(
    version: str = "3.3",
    gw_id: str = "gw001",
    available: bool = True,
) -> MagicMock:
    coord = MagicMock()
    coord.gw_id = gw_id
    coord.version = version
    coord.device_name = gw_id
    coord.profile_name = ""
    coord.state = DeviceState(available=available, dps={})
    coord.async_send_dps = AsyncMock()
    return coord


def _make_entity(
    version: str = "3.3", gw_id: str = "gw001", available: bool = True
) -> TuyaCloudlessUpdateEntity:
    coord = _make_coordinator(version=version, gw_id=gw_id, available=available)
    entity = TuyaCloudlessUpdateEntity.__new__(TuyaCloudlessUpdateEntity)
    entity.coordinator = coord
    entity._dp_id = None
    entity._attr_unique_id = f"{coord.gw_id}_update_firmware"
    entity._attr_translation_key = "protocol_version"
    return entity


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestTuyaCloudlessUpdateEntity:
    """Unit tests for TuyaCloudlessUpdateEntity."""

    def test_installed_version_returns_protocol_version(self) -> None:
        entity = _make_entity(version="3.3")
        assert entity.installed_version == "3.3"

    def test_latest_version_equals_installed_version(self) -> None:
        entity = _make_entity(version="3.4")
        assert entity.latest_version == entity.installed_version

    def test_no_update_available(self) -> None:
        """latest_version == installed_version means no pending update."""
        entity = _make_entity(version="3.5")
        assert entity.latest_version == "3.5"
        assert entity.installed_version == "3.5"

    def test_supported_features_zero(self) -> None:
        """No OTA install feature should be advertised."""
        from homeassistant.components.update import UpdateEntityFeature

        entity = _make_entity()
        assert entity._attr_supported_features == 0
        assert not (entity._attr_supported_features & UpdateEntityFeature.INSTALL)

    def test_device_class_is_none(self) -> None:
        """HA-003 / PLAT-843: device_class must be None (not FIRMWARE).

        The entity shows a protocol version, not actual firmware, so using
        UpdateDeviceClass.FIRMWARE would be misleading.
        """
        entity = _make_entity()
        assert entity._attr_device_class is None

    def test_entity_category_diagnostic(self) -> None:
        from homeassistant.const import EntityCategory

        entity = _make_entity()
        assert entity._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_unique_id_format(self) -> None:
        entity = _make_entity(gw_id="abc123")
        assert entity._attr_unique_id == "abc123_update_firmware"

    def test_available_when_device_connected(self) -> None:
        entity = _make_entity(available=True)
        assert entity.available is True

    def test_unavailable_when_device_disconnected(self) -> None:
        entity = _make_entity(available=False)
        assert entity.available is False

    def test_all_protocol_versions(self) -> None:
        """Verify entity works for all valid protocol versions."""
        for version in ("3.1", "3.2", "3.3", "3.4", "3.5"):
            entity = _make_entity(version=version)
            assert entity.installed_version == version
            assert entity.latest_version == version

    def test_translation_key(self) -> None:
        """HA-003 / PLAT-843: translation key must be protocol_version."""
        entity = _make_entity()
        assert entity._attr_translation_key == "protocol_version"


# ── async_setup_entry ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity() -> None:
    """setup_entry should register exactly one TuyaCloudlessUpdateEntity."""
    from custom_components.tuya_cloudless.update import async_setup_entry
    from tests.conftest import FakeConfigEntry

    coordinator = _make_coordinator()

    runtime = MagicMock()
    runtime.coordinator = coordinator

    entry = FakeConfigEntry()
    entry.runtime_data = runtime

    added: list[Any] = []
    async_add_entities = MagicMock(side_effect=lambda entities: added.extend(entities))

    await async_setup_entry(MagicMock(), entry, async_add_entities)

    assert len(added) == 1
    assert isinstance(added[0], TuyaCloudlessUpdateEntity)


@pytest.mark.asyncio
async def test_async_setup_entry_entity_has_correct_version() -> None:
    """Entity created by setup_entry should reflect coordinator version."""
    from custom_components.tuya_cloudless.update import async_setup_entry
    from tests.conftest import FakeConfigEntry

    coordinator = _make_coordinator(version="3.5", gw_id="testgw")

    runtime = MagicMock()
    runtime.coordinator = coordinator

    entry = FakeConfigEntry()
    entry.runtime_data = runtime

    added: list[Any] = []
    async_add_entities = MagicMock(side_effect=lambda entities: added.extend(entities))

    await async_setup_entry(MagicMock(), entry, async_add_entities)

    entity: TuyaCloudlessUpdateEntity = added[0]
    assert entity.installed_version == "3.5"
    assert entity.latest_version == "3.5"
    assert entity._attr_unique_id == "testgw_update_firmware"
