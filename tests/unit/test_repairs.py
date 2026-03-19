"""Unit tests for Tuya Cloudless repair flows."""

from __future__ import annotations

from unittest.mock import MagicMock

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


# ── async_create_fix_flow — entry_id extraction ───────────────────────────────


class TestCreateFixFlowEntryId:
    @pytest.mark.asyncio
    async def test_entry_id_from_data_field(self) -> None:
        """entry_id is taken from data['entry_id'] when present."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessAuthRepairFlow,
            async_create_fix_flow,
        )

        data = {"entry_id": "abc123"}
        flow = await async_create_fix_flow(None, "auth_failed_other", data)  # type: ignore[arg-type]
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)
        assert flow._entry_id == "abc123"

    @pytest.mark.asyncio
    async def test_entry_id_extracted_from_issue_id_string(self) -> None:
        """entry_id falls back to the part after the 'auth_failed_' prefix in issue_id."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessAuthRepairFlow,
            async_create_fix_flow,
        )

        flow = await async_create_fix_flow(None, "auth_failed_entry999", None)  # type: ignore[arg-type]
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)
        assert flow._entry_id == "entry999"

    @pytest.mark.asyncio
    async def test_connectivity_entry_id_from_data(self) -> None:
        """Connectivity flow picks up entry_id from data dict."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
            async_create_fix_flow,
        )

        data = {"entry_id": "conn_entry_42"}
        flow = await async_create_fix_flow(None, "connectivity_x", data)  # type: ignore[arg-type]
        assert isinstance(flow, TuyaCloudlessConnectivityRepairFlow)
        assert flow._entry_id == "conn_entry_42"


# ── TuyaCloudlessAuthRepairFlow ───────────────────────────────────────────────


class TestAuthRepairFlow:
    def _make_flow(self, entry_id: str = "eid1") -> object:
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        flow = TuyaCloudlessAuthRepairFlow(entry_id)
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        return flow

    @pytest.mark.asyncio
    async def test_async_step_init_no_input_shows_form(self) -> None:
        """async_step_init(None) delegates to async_step_confirm which shows a form."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        flow = self._make_flow()
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)
        result = await flow.async_step_init(None)
        flow.async_show_form.assert_called_once()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_async_step_confirm_no_input_shows_form(self) -> None:
        """async_step_confirm(None) returns a form."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        flow = self._make_flow()
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)
        result = await flow.async_step_confirm(None)
        flow.async_show_form.assert_called_once()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_async_step_confirm_show_form_uses_confirm_step_id(self) -> None:
        """async_show_form is called with step_id='confirm'."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        flow = self._make_flow()
        assert isinstance(flow, TuyaCloudlessAuthRepairFlow)
        await flow.async_step_confirm(None)
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "confirm"

    @pytest.mark.asyncio
    async def test_async_step_confirm_with_input_creates_entry(self) -> None:
        """async_step_confirm({}) with a hass entry triggers reauth and creates entry."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        entry = MagicMock()
        entry.async_start_reauth = MagicMock()
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry

        flow = TuyaCloudlessAuthRepairFlow("test_entry")
        flow.hass = hass
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        result = await flow.async_step_confirm(user_input={})
        flow.async_create_entry.assert_called_once_with(data={})
        entry.async_start_reauth.assert_called_once_with(hass)
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_async_step_confirm_with_input_entry_none_still_creates(self) -> None:
        """async_step_confirm({}) completes even when config entry is not found."""
        from custom_components.tuya_cloudless.repairs import TuyaCloudlessAuthRepairFlow

        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = None

        flow = TuyaCloudlessAuthRepairFlow("missing_entry")
        flow.hass = hass
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        result = await flow.async_step_confirm(user_input={})
        flow.async_create_entry.assert_called_once_with(data={})
        assert result["type"] == "create_entry"


# ── TuyaCloudlessConnectivityRepairFlow ───────────────────────────────────────


class TestConnectivityRepairFlow:
    def _make_flow(
        self,
        entry_id: str = "eid2",
        data: dict | None = None,
    ) -> object:
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
        )

        flow = TuyaCloudlessConnectivityRepairFlow(entry_id, data)
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        return flow

    @pytest.mark.asyncio
    async def test_async_step_init_no_input_shows_form(self) -> None:
        """async_step_init(None) delegates to async_step_confirm → shows form."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
        )

        flow = self._make_flow()
        assert isinstance(flow, TuyaCloudlessConnectivityRepairFlow)
        result = await flow.async_step_init(None)
        flow.async_show_form.assert_called_once()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_async_step_confirm_no_input_shows_form(self) -> None:
        """async_step_confirm(None) shows the form with description placeholders."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
        )

        flow = self._make_flow(data={"last_seen": "2026-01-01", "reconnect_count": 5})
        assert isinstance(flow, TuyaCloudlessConnectivityRepairFlow)
        result = await flow.async_step_confirm(None)
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "confirm"
        placeholders = call_kwargs["description_placeholders"]
        assert placeholders["last_seen"] == "2026-01-01"
        assert placeholders["reconnect_count"] == "5"
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_async_step_confirm_default_placeholders(self) -> None:
        """async_step_confirm with no issue_data uses default placeholder values."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
        )

        flow = self._make_flow(data=None)
        assert isinstance(flow, TuyaCloudlessConnectivityRepairFlow)
        await flow.async_step_confirm(None)
        call_kwargs = flow.async_show_form.call_args[1]
        placeholders = call_kwargs["description_placeholders"]
        assert placeholders["last_seen"] == "unknown"
        assert placeholders["reconnect_count"] == "0"

    @pytest.mark.asyncio
    async def test_async_step_confirm_with_input_creates_entry(self) -> None:
        """async_step_confirm({}) with a hass entry triggers reconfigure and creates entry."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
        )

        entry = MagicMock()
        entry.async_start_reconfiguration = MagicMock()
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry

        flow = TuyaCloudlessConnectivityRepairFlow("test_entry", None)
        flow.hass = hass
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        result = await flow.async_step_confirm(user_input={})
        flow.async_create_entry.assert_called_once_with(data={})
        entry.async_start_reconfiguration.assert_called_once_with(hass)
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_async_step_confirm_entry_missing_reconfigure_attr(self) -> None:
        """async_step_confirm({}) completes gracefully when entry has no reconfigure method."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
        )

        # Entry without async_start_reconfiguration attribute
        entry = MagicMock(spec=["other_attr"])
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry

        flow = TuyaCloudlessConnectivityRepairFlow("test_entry", None)
        flow.hass = hass
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        result = await flow.async_step_confirm(user_input={})
        flow.async_create_entry.assert_called_once_with(data={})
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_async_step_confirm_entry_none_still_creates(self) -> None:
        """async_step_confirm({}) completes even when config entry is not found."""
        from custom_components.tuya_cloudless.repairs import (
            TuyaCloudlessConnectivityRepairFlow,
        )

        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = None

        flow = TuyaCloudlessConnectivityRepairFlow("missing_entry", None)
        flow.hass = hass
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        result = await flow.async_step_confirm(user_input={})
        flow.async_create_entry.assert_called_once_with(data={})
        assert result["type"] == "create_entry"
