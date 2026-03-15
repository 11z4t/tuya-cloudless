"""Unit tests for Tuya Cloudless repair flows."""

from __future__ import annotations

import pytest


class TestRepairFlows:
    @pytest.mark.asyncio
    async def test_async_create_fix_flow_auth_failure(self) -> None:
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessAuthRepairFlow,
            async_create_fix_flow,
        )

        flow = await async_create_fix_flow(None, "auth_failure", None)  # type: ignore[arg-type]
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)

    @pytest.mark.asyncio
    async def test_async_create_fix_flow_connectivity(self) -> None:
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
            async_create_fix_flow,
        )

        flow = await async_create_fix_flow(None, "connectivity", None)  # type: ignore[arg-type]
        assert isinstance(flow, TuyaCloudlessConnectivityRepairFlow)

    @pytest.mark.asyncio
    async def test_async_create_fix_flow_unknown_returns_auth_flow(self) -> None:
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessAuthRepairFlow,
            async_create_fix_flow,
        )

        flow = await async_create_fix_flow(None, "some_unknown_issue", None)  # type: ignore[arg-type]
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)

    @pytest.mark.asyncio
    async def test_async_create_fix_flow_with_data(self) -> None:
        from custom_components.tuya_cloudless.repairs import async_create_fix_flow

        data = {"gw_id": "gw001", "ip": "192.168.1.100"}
        flow = await async_create_fix_flow(None, "auth_failure", data)  # type: ignore[arg-type]
        assert flow is not None
