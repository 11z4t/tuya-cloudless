"""Tests for custom_components.tuya_cloudless.config_flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.tuya_cloudless.config_flow import (
    TuyaCloudlessConfigFlow,
    TuyaCloudlessOptionsFlow,
    _LOCAL_KEY_LENGTH,
    _get_profile_options,
)
from custom_components.tuya_cloudless.const import (
    CONF_DEVICE_NAME,
    CONF_GW_ID,
    CONF_IP_ADDRESS,
    CONF_LOCAL_KEY,
    CONF_OPT_COMMAND_TIMEOUT,
    CONF_OPT_HEARTBEAT_INTERVAL,
    CONF_OPT_RECONNECT_MAX_DELAY,
    CONF_PROFILE,
    CONF_PROTOCOL_VERSION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PROTOCOL_VERSION,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_flow() -> TuyaCloudlessConfigFlow:
    flow = TuyaCloudlessConfigFlow()
    flow.hass = MagicMock()
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    return flow


# ── Config flow version ───────────────────────────────────────────────────────


class TestConfigFlowVersion:
    def test_version(self) -> None:
        assert TuyaCloudlessConfigFlow.VERSION == CONFIG_ENTRY_VERSION

    def test_local_key_length(self) -> None:
        assert _LOCAL_KEY_LENGTH == 16


# ── _get_profile_options ──────────────────────────────────────────────────────


class TestGetProfileOptions:
    def test_returns_profiles(self) -> None:
        mock_profile = MagicMock()
        mock_profile.name = "Test Profile"
        with patch(
            "tuya_cloudless.profiles.list_profiles",
            return_value=[mock_profile],
        ):
            options = _get_profile_options()
        assert len(options) == 1
        assert options[0]["value"] == "Test Profile"

    def test_fallback_when_no_profiles(self) -> None:
        call_count = 0

        def mock_list() -> list:
            nonlocal call_count
            call_count += 1
            return []

        with patch("tuya_cloudless.profiles.list_profiles", side_effect=mock_list):
            with patch("tuya_cloudless.profiles.init_profiles"):
                options = _get_profile_options()
        assert len(options) == 1
        assert options[0]["value"] == "Generic Switch"


# ── Step: user ─────────────────────────────────────────────────────────────────


class TestStepUser:
    @pytest.mark.asyncio
    async def test_show_form_first_render(self) -> None:
        flow = _make_flow()
        result = await flow.async_step_user(None)
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_manual_mode_goes_to_manual(self) -> None:
        flow = _make_flow()
        flow.async_step_manual = AsyncMock(return_value={"type": "form"})
        await flow.async_step_user({"setup_mode": "manual"})
        flow.async_step_manual.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_no_devices(self) -> None:
        flow = _make_flow()
        flow._run_discovery = AsyncMock(return_value=[])
        await flow.async_step_user({"setup_mode": "search"})
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"]["base"] == "no_devices_found"

    @pytest.mark.asyncio
    async def test_search_timeout(self) -> None:
        flow = _make_flow()

        async def slow_discovery() -> list:
            raise TimeoutError

        flow._run_discovery = slow_discovery  # type: ignore[assignment]
        await flow.async_step_user({"setup_mode": "search"})
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"]["base"] == "no_devices_found"

    @pytest.mark.asyncio
    async def test_search_oserror(self) -> None:
        flow = _make_flow()
        flow._run_discovery = AsyncMock(side_effect=OSError("bind failed"))
        await flow.async_step_user({"setup_mode": "search"})
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"]["base"] == "no_devices_found"

    @pytest.mark.asyncio
    async def test_search_found_devices(self) -> None:
        flow = _make_flow()
        flow._run_discovery = AsyncMock(
            return_value=[
                {
                    CONF_GW_ID: "dev1",
                    CONF_IP_ADDRESS: "1.2.3.4",
                    CONF_PROTOCOL_VERSION: "3.3",
                }
            ]
        )
        flow.async_step_select = AsyncMock(return_value={"type": "form"})
        await flow.async_step_user({"setup_mode": "search"})
        flow.async_step_select.assert_awaited_once()


# ── Step: select ───────────────────────────────────────────────────────────────


class TestStepSelect:
    @pytest.mark.asyncio
    async def test_no_discovered_redirects_manual(self) -> None:
        flow = _make_flow()
        flow._discovered = []
        flow.async_step_manual = AsyncMock(return_value={"type": "form"})
        await flow.async_step_select(None)
        flow.async_step_manual.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_show_form(self) -> None:
        flow = _make_flow()
        flow._discovered = [
            {CONF_GW_ID: "dev1", CONF_IP_ADDRESS: "1.2.3.4", CONF_PROTOCOL_VERSION: "3.3"}
        ]
        await flow.async_step_select(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_select_manual(self) -> None:
        flow = _make_flow()
        flow._discovered = [
            {CONF_GW_ID: "dev1", CONF_IP_ADDRESS: "1.2.3.4", CONF_PROTOCOL_VERSION: "3.3"}
        ]
        flow.async_step_manual = AsyncMock(return_value={"type": "form"})
        await flow.async_step_select({"device": "__manual__"})
        flow.async_step_manual.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_select_device(self) -> None:
        flow = _make_flow()
        flow._discovered = [
            {CONF_GW_ID: "dev1", CONF_IP_ADDRESS: "1.2.3.4", CONF_PROTOCOL_VERSION: "3.3"}
        ]
        flow.async_step_local_key = AsyncMock(return_value={"type": "form"})
        await flow.async_step_select({"device": "dev1"})
        flow.async_step_local_key.assert_awaited_once()
        assert flow._device[CONF_GW_ID] == "dev1"

    @pytest.mark.asyncio
    async def test_select_nonexistent_device(self) -> None:
        flow = _make_flow()
        flow._discovered = [
            {CONF_GW_ID: "dev1", CONF_IP_ADDRESS: "1.2.3.4", CONF_PROTOCOL_VERSION: "3.3"}
        ]
        await flow.async_step_select({"device": "nonexistent"})
        flow.async_show_form.assert_called()


# ── Step: local_key ────────────────────────────────────────────────────────────


class TestStepLocalKey:
    @pytest.mark.asyncio
    async def test_show_form(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_IP_ADDRESS: "1.2.3.4", CONF_PROTOCOL_VERSION: "3.3"}
        with patch(
            "custom_components.tuya_cloudless.config_flow._get_profile_options",
            return_value=[{"value": "Generic Switch", "label": "Generic Switch"}],
        ):
            await flow.async_step_local_key(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_key_length(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_IP_ADDRESS: "1.2.3.4"}
        await flow.async_step_local_key({CONF_LOCAL_KEY: "short"})
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"][CONF_LOCAL_KEY] == "invalid_local_key"

    @pytest.mark.asyncio
    async def test_valid_key_with_profile(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_IP_ADDRESS: "1.2.3.4", CONF_GW_ID: "dev1"}
        flow.async_step_confirm = AsyncMock(return_value={"type": "form"})
        await flow.async_step_local_key({
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_DEVICE_NAME: "My Light",
            CONF_PROFILE: "Generic Light",
        })
        flow.async_step_confirm.assert_awaited_once()
        assert flow._device[CONF_LOCAL_KEY] == "0123456789abcdef"
        assert flow._device[CONF_DEVICE_NAME] == "My Light"
        assert flow._device[CONF_PROFILE] == "Generic Light"

    @pytest.mark.asyncio
    async def test_valid_key_empty_name(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_IP_ADDRESS: "1.2.3.4", CONF_GW_ID: "dev1"}
        flow.async_step_confirm = AsyncMock(return_value={"type": "form"})
        await flow.async_step_local_key({
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_DEVICE_NAME: "",
        })
        assert flow._device[CONF_DEVICE_NAME] == "1.2.3.4"  # Fallback to IP


# ── Step: confirm ──────────────────────────────────────────────────────────────


class TestStepConfirm:
    @pytest.mark.asyncio
    async def test_show_form(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_GW_ID: "dev1", CONF_IP_ADDRESS: "1.2.3.4"}
        await flow.async_step_confirm(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_failure(self) -> None:
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "dev1",
            CONF_IP_ADDRESS: "1.2.3.4",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }
        flow._check_connection = AsyncMock(
            return_value={CONF_IP_ADDRESS: "cannot_connect"}
        )
        await flow.async_step_confirm({})
        flow.async_show_form.assert_called()

    @pytest.mark.asyncio
    async def test_connection_success(self) -> None:
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "dev1",
            CONF_IP_ADDRESS: "1.2.3.4",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_PROFILE: "Generic Switch",
            CONF_DEVICE_NAME: "My Device",
        }
        flow._check_connection = AsyncMock(return_value={})
        await flow.async_step_confirm({})
        flow.async_create_entry.assert_called_once()
        call_data = flow.async_create_entry.call_args[1]["data"]
        assert call_data[CONF_PROFILE] == "Generic Switch"


# ── Step: manual ───────────────────────────────────────────────────────────────


class TestStepManual:
    @pytest.mark.asyncio
    async def test_show_form(self) -> None:
        flow = _make_flow()
        with patch(
            "custom_components.tuya_cloudless.config_flow._get_profile_options",
            return_value=[{"value": "Generic Switch", "label": "Generic Switch"}],
        ):
            await flow.async_step_manual(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_gw_id(self) -> None:
        flow = _make_flow()
        await flow.async_step_manual({
            CONF_GW_ID: "",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_IP_ADDRESS: "1.2.3.4",
        })
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"][CONF_GW_ID] == "invalid_gw_id"

    @pytest.mark.asyncio
    async def test_invalid_local_key(self) -> None:
        flow = _make_flow()
        await flow.async_step_manual({
            CONF_GW_ID: "dev1",
            CONF_LOCAL_KEY: "short",
            CONF_IP_ADDRESS: "1.2.3.4",
        })
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"][CONF_LOCAL_KEY] == "invalid_local_key"

    @pytest.mark.asyncio
    async def test_empty_ip(self) -> None:
        flow = _make_flow()
        await flow.async_step_manual({
            CONF_GW_ID: "dev1",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_IP_ADDRESS: "",
        })
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"][CONF_IP_ADDRESS] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_connection_fail(self) -> None:
        flow = _make_flow()
        flow._check_connection = AsyncMock(
            return_value={CONF_IP_ADDRESS: "cannot_connect"}
        )
        await flow.async_step_manual({
            CONF_GW_ID: "dev1",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_IP_ADDRESS: "1.2.3.4",
        })
        call_kwargs = flow.async_show_form.call_args[1]
        assert "cannot_connect" in str(call_kwargs["errors"])

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        flow = _make_flow()
        flow._check_connection = AsyncMock(return_value={})
        await flow.async_step_manual({
            CONF_GW_ID: "dev1",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_IP_ADDRESS: "1.2.3.4",
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_PROFILE: "Generic Switch",
            CONF_DEVICE_NAME: "My Switch",
        })
        flow.async_create_entry.assert_called_once()
        call_data = flow.async_create_entry.call_args[1]["data"]
        assert call_data[CONF_PROFILE] == "Generic Switch"

    @pytest.mark.asyncio
    async def test_success_no_name(self) -> None:
        flow = _make_flow()
        flow._check_connection = AsyncMock(return_value={})
        await flow.async_step_manual({
            CONF_GW_ID: "dev1",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_IP_ADDRESS: "1.2.3.4",
        })
        flow.async_create_entry.assert_called_once()
        call_kwargs = flow.async_create_entry.call_args[1]
        assert call_kwargs["title"] == "1.2.3.4"


# ── Step: discovery ────────────────────────────────────────────────────────────


class TestStepDiscovery:
    @pytest.mark.asyncio
    async def test_show_form(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_IP_ADDRESS: "1.2.3.4", CONF_PROTOCOL_VERSION: "3.3"}
        with patch(
            "custom_components.tuya_cloudless.config_flow._get_profile_options",
            return_value=[{"value": "Generic Switch", "label": "Generic Switch"}],
        ):
            await flow.async_step_discovery(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_key(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_IP_ADDRESS: "1.2.3.4"}
        await flow.async_step_discovery({CONF_LOCAL_KEY: "short"})
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"][CONF_LOCAL_KEY] == "invalid_local_key"

    @pytest.mark.asyncio
    async def test_valid_key(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_GW_ID: "dev1", CONF_IP_ADDRESS: "1.2.3.4"}
        flow.async_step_confirm = AsyncMock(return_value={"type": "form"})
        await flow.async_step_discovery({
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_DEVICE_NAME: "My Discovered",
            CONF_PROFILE: "Generic Light",
        })
        flow.async_step_confirm.assert_awaited_once()
        assert flow._device[CONF_DEVICE_NAME] == "My Discovered"
        assert flow._device[CONF_PROFILE] == "Generic Light"

    @pytest.mark.asyncio
    async def test_valid_key_empty_name(self) -> None:
        flow = _make_flow()
        flow._device = {CONF_GW_ID: "dev1", CONF_IP_ADDRESS: "1.2.3.4"}
        flow.async_step_confirm = AsyncMock(return_value={"type": "form"})
        await flow.async_step_discovery({
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_DEVICE_NAME: "",
        })
        assert flow._device[CONF_DEVICE_NAME] == "1.2.3.4"


# ── Step: reauth ──────────────────────────────────────────────────────────────


class TestStepReauth:
    @pytest.mark.asyncio
    async def test_reauth_delegates_to_confirm(self) -> None:
        flow = _make_flow()
        flow.async_step_reauth_confirm = AsyncMock(return_value={"type": "form"})
        await flow.async_step_reauth({"gw_id": "dev1"})
        flow.async_step_reauth_confirm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reauth_confirm_show_form(self) -> None:
        flow = _make_flow()
        reauth_entry = MagicMock()
        reauth_entry.data = {CONF_IP_ADDRESS: "1.2.3.4"}
        reauth_entry.title = "Test"
        flow._get_reauth_entry = MagicMock(return_value=reauth_entry)
        await flow.async_step_reauth_confirm(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_reauth_confirm_invalid_key(self) -> None:
        flow = _make_flow()
        reauth_entry = MagicMock()
        reauth_entry.data = {CONF_IP_ADDRESS: "1.2.3.4"}
        reauth_entry.title = "Test"
        flow._get_reauth_entry = MagicMock(return_value=reauth_entry)
        await flow.async_step_reauth_confirm({CONF_LOCAL_KEY: "short"})
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"][CONF_LOCAL_KEY] == "invalid_local_key"

    @pytest.mark.asyncio
    async def test_reauth_confirm_connection_fail(self) -> None:
        flow = _make_flow()
        reauth_entry = MagicMock()
        reauth_entry.data = {CONF_IP_ADDRESS: "1.2.3.4"}
        reauth_entry.title = "Test"
        flow._get_reauth_entry = MagicMock(return_value=reauth_entry)
        flow._check_connection = AsyncMock(
            return_value={CONF_IP_ADDRESS: "cannot_connect"}
        )
        await flow.async_step_reauth_confirm({
            CONF_LOCAL_KEY: "0123456789abcdef",
        })
        flow.async_show_form.assert_called()

    @pytest.mark.asyncio
    async def test_reauth_confirm_success(self) -> None:
        flow = _make_flow()
        reauth_entry = MagicMock()
        reauth_entry.data = {
            CONF_GW_ID: "dev1",
            CONF_IP_ADDRESS: "1.2.3.4",
            CONF_LOCAL_KEY: "old_key_1234abcd",
        }
        reauth_entry.title = "Test"
        flow._get_reauth_entry = MagicMock(return_value=reauth_entry)
        flow._check_connection = AsyncMock(return_value={})
        flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
        await flow.async_step_reauth_confirm({
            CONF_LOCAL_KEY: "0123456789abcdef",
        })
        flow.async_update_reload_and_abort.assert_called_once()


# ── Check connection ───────────────────────────────────────────────────────────


class TestCheckConnection:
    @pytest.mark.asyncio
    async def test_connection_success(self) -> None:
        flow = _make_flow()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        with patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = (MagicMock(), writer)
            errors = await flow._check_connection("1.2.3.4")
        assert errors == {}

    @pytest.mark.asyncio
    async def test_connection_timeout(self) -> None:
        flow = _make_flow()
        with patch("asyncio.wait_for", side_effect=TimeoutError):
            errors = await flow._check_connection("1.2.3.4")
        assert errors[CONF_IP_ADDRESS] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_connection_oserror(self) -> None:
        flow = _make_flow()
        with patch("asyncio.wait_for", side_effect=OSError("refused")):
            errors = await flow._check_connection("1.2.3.4")
        assert errors[CONF_IP_ADDRESS] == "cannot_connect"


# ── Options flow ───────────────────────────────────────────────────────────────


class TestOptionsFlow:
    def _make_options_flow(
        self,
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> TuyaCloudlessOptionsFlow:
        entry = MagicMock()
        entry.data = data or {
            CONF_IP_ADDRESS: "1.2.3.4",
            CONF_PROTOCOL_VERSION: "3.3",
        }
        entry.options = options or {}

        # Use __new__ to bypass __init__ checks, then set _config_entry for
        # the OptionsFlow.config_entry property to find it
        flow = TuyaCloudlessOptionsFlow.__new__(TuyaCloudlessOptionsFlow)
        flow._config_entry = entry
        flow.hass = MagicMock()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        return flow

    @pytest.mark.asyncio
    async def test_show_form(self) -> None:
        flow = self._make_options_flow()
        result = await flow.async_step_init(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_options(self) -> None:
        flow = self._make_options_flow()
        result = await flow.async_step_init({
            CONF_IP_ADDRESS: "10.0.0.1",
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_OPT_HEARTBEAT_INTERVAL: 30,
            CONF_OPT_COMMAND_TIMEOUT: 10,
            CONF_OPT_RECONNECT_MAX_DELAY: 120,
        })
        flow.async_create_entry.assert_called_once()
        call_data = flow.async_create_entry.call_args[1]["data"]
        assert call_data[CONF_OPT_HEARTBEAT_INTERVAL] == 30


# ── Options flow getter ───────────────────────────────────────────────────────


class TestGetOptionsFlow:
    def test_returns_options_flow(self) -> None:
        entry = MagicMock()
        flow = TuyaCloudlessConfigFlow.async_get_options_flow(entry)
        assert isinstance(flow, TuyaCloudlessOptionsFlow)
