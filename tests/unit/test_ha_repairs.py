"""Tests for custom_components.tuya_cloudless.repairs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestRepairs:
    @pytest.mark.asyncio
    async def test_create_fix_flow_returns_flow(self) -> None:
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessAuthRepairFlow,
            async_create_fix_flow,
        )

        hass = MagicMock()
        flow = await async_create_fix_flow(hass, "auth_failed_test", None)
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)

    @pytest.mark.asyncio
    async def test_create_fix_flow_with_data(self) -> None:
        from custom_components.tuya_cloudless.repairs import async_create_fix_flow

        hass = MagicMock()
        flow = await async_create_fix_flow(hass, "auth_failed_test", {"device_name": "Test"})
        assert flow is not None

    def test_auth_repair_flow_is_repairs_flow(self) -> None:
        from homeassistant.components.repairs import RepairsFlow

        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        assert issubclass(TuyaCloudlessAuthRepairFlow, RepairsFlow)

    @pytest.mark.asyncio
    async def test_auth_repair_flow_triggers_reauth(self) -> None:
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        entry = MagicMock()
        entry.async_start_reauth = MagicMock()
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry

        flow = TuyaCloudlessAuthRepairFlow("test_entry_id")
        flow.hass = hass
        result = await flow.async_step_confirm(user_input={})
        assert result["type"] == "create_entry"
        entry.async_start_reauth.assert_called_once_with(hass)

    @pytest.mark.asyncio
    async def test_connectivity_repair_flow_calls_start_reconfiguration(self) -> None:
        """HA-002 / PLAT-842: confirm should call async_start_reconfiguration if available."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessConnectivityRepairFlow

        entry = MagicMock(spec=["async_start_reconfiguration"])
        entry.async_start_reconfiguration = MagicMock()
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry

        flow = TuyaCloudlessConnectivityRepairFlow("test_entry_id", {})
        flow.hass = hass
        result = await flow.async_step_confirm(user_input={})
        assert result["type"] == "create_entry"
        entry.async_start_reconfiguration.assert_called_once_with(hass)

    @pytest.mark.asyncio
    async def test_connectivity_repair_flow_skips_reconfiguration_if_unavailable(self) -> None:
        """HA-002: gracefully skip if async_start_reconfiguration not on entry object."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessConnectivityRepairFlow

        # entry without async_start_reconfiguration attribute
        entry = MagicMock(spec=["async_start_reauth"])
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry

        flow = TuyaCloudlessConnectivityRepairFlow("test_entry_id", {})
        flow.hass = hass
        # Must not raise even when async_start_reconfiguration is absent
        result = await flow.async_step_confirm(user_input={})
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_connectivity_repair_flow_shows_form_when_no_input(self) -> None:
        """Connectivity repair flow shows form when user_input is None."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessConnectivityRepairFlow

        hass = MagicMock()
        flow = TuyaCloudlessConnectivityRepairFlow(
            "test_entry_id", {"last_seen": "2026-01-01", "reconnect_count": 5}
        )
        flow.hass = hass
        result = await flow.async_step_confirm(user_input=None)
        assert result["type"] == "form"
