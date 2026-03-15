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

    def test_auth_repair_flow_is_confirm_flow(self) -> None:
        from homeassistant.components.repairs import ConfirmRepairFlow

        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        assert issubclass(TuyaCloudlessAuthRepairFlow, ConfirmRepairFlow)
