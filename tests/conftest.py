"""Shared test fixtures for Tuya Cloudless tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tuya_cloudless.profiles import DPSpec, EntitySpec

# ── Fake HomeAssistant objects ───────────────────────────────────────────────


class FakeConfigEntry:
    """Minimal mock of homeassistant.config_entries.ConfigEntry."""

    def __init__(
        self,
        *,
        entry_id: str = "test_entry_id",
        domain: str = "tuya_cloudless",
        title: str = "Test Device",
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        version: int = 1,
    ) -> None:
        self.entry_id = entry_id
        self.domain = domain
        self.title = title
        self.data = data or {
            "gw_id": "abc123def456",
            "local_key": "0123456789abcdef",
            "ip_address": "192.168.1.42",
            "protocol_version": "3.3",
            "device_type": "generic",
            "device_name": "Test Device",
            "profile": "Generic Switch",
        }
        self.options = options or {}
        self.version = version
        self.runtime_data: Any = None
        self._update_listeners: list[Any] = []

    def add_update_listener(self, listener: Any) -> Any:
        self._update_listeners.append(listener)
        return lambda: self._update_listeners.remove(listener)

    def async_on_unload(self, callback: Any) -> None:
        pass


@pytest.fixture
def config_entry() -> FakeConfigEntry:
    """Return a fake config entry with default data."""
    return FakeConfigEntry()


@pytest.fixture
def hass() -> MagicMock:
    """Return a minimal mock of HomeAssistant."""
    mock_hass = MagicMock()
    mock_hass.config_entries = MagicMock()
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    mock_hass.config_entries.async_reload = AsyncMock()
    mock_hass.config_entries.async_update_entry = MagicMock()
    mock_hass.async_create_task = MagicMock(
        side_effect=lambda coro, **kw: asyncio.ensure_future(coro)
    )
    return mock_hass


# ── EntitySpec helpers ───────────────────────────────────────────────────────


def make_switch_spec(dp_id: str = "1", name: str = "main_switch") -> EntitySpec:
    """Return a switch EntitySpec."""
    return EntitySpec(
        platform="switch",
        name=name,
        dp_power=DPSpec(id=dp_id, type="bool"),
    )


def make_sensor_spec(dp_id: str = "19", name: str = "power_consumption") -> EntitySpec:
    """Return a sensor EntitySpec with scale."""
    return EntitySpec(
        platform="sensor",
        name=name,
        dp_value=DPSpec(id=dp_id, type="int", scale=0.1),
        device_class="power",
        state_class="measurement",
        unit="W",
    )


def make_light_spec(
    dp_power_id: str = "1",
    dp_brightness_id: str | None = "2",
    dp_color_temp_id: str | None = "3",
    dp_hs_hue_id: str | None = None,
    dp_hs_sat_id: str | None = None,
    dp_color_mode_id: str | None = None,
    effects: tuple[str, ...] = (),
    name: str = "main_light",
) -> EntitySpec:
    """Return a light EntitySpec."""
    return EntitySpec(
        platform="light",
        name=name,
        dp_power=DPSpec(id=dp_power_id, type="bool"),
        dp_brightness=DPSpec(id=dp_brightness_id, type="int", min_raw=10, max_raw=1000)
        if dp_brightness_id
        else None,
        dp_color_temp=DPSpec(id=dp_color_temp_id, type="int", min_raw=0, max_raw=1000)
        if dp_color_temp_id
        else None,
        dp_hs_hue=DPSpec(id=dp_hs_hue_id, type="int", max_raw=360) if dp_hs_hue_id else None,
        dp_hs_saturation=DPSpec(id=dp_hs_sat_id, type="int", max_raw=1000)
        if dp_hs_sat_id
        else None,
        dp_color_mode=DPSpec(id=dp_color_mode_id, type="enum") if dp_color_mode_id else None,
        effects=effects,
    )


def make_binary_sensor_spec(dp_id: str = "26", name: str = "overload") -> EntitySpec:
    """Return a binary_sensor EntitySpec."""
    return EntitySpec(
        platform="binary_sensor",
        name=name,
        dp_power=DPSpec(id=dp_id, type="bool"),
        device_class="problem",
    )


def make_cover_spec(
    dp_open_id: str = "1",
    dp_position_id: str | None = "2",
    dp_tilt_id: str | None = None,
    name: str = "cover",
) -> EntitySpec:
    """Return a cover EntitySpec."""
    return EntitySpec(
        platform="cover",
        name=name,
        dp_open=DPSpec(id=dp_open_id, type="bool"),
        dp_position=DPSpec(id=dp_position_id, type="int") if dp_position_id else None,
        dp_tilt=DPSpec(id=dp_tilt_id, type="int") if dp_tilt_id else None,
        device_class="blind",
    )


def make_coordinator(
    dps: dict[str, Any] | None = None,
    gw_id: str = "gw001",
    available: bool = True,
) -> MagicMock:
    """Return a minimal coordinator mock with pre-set DPS state."""
    coord = MagicMock()
    coord._gw_id = gw_id
    coord.gw_id = gw_id
    coord.device_name = gw_id
    coord.profile_name = ""
    coord._version = "3.3"
    coord.version = "3.3"
    coord.state = MagicMock()
    coord.state.available = available
    coord.state.dps = dps or {}
    coord.state.last_seen = None
    coord.state.reconnect_count = 0
    coord.state.last_error = None
    coord.async_send_dps = AsyncMock()
    return coord
