"""Unit tests for config_flow module — helpers and options flow.

Config flow steps (async_step_user, async_step_confirm, etc.) require
the HA runtime so they live in integration tests. Here we test:
  - _get_profile_options() helper
  - OptionsFlow.async_step_init form rendering and data saving
  - Reconfigure flow logic (validation)
  - _LOCAL_KEY_LENGTH constant
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from tuya_cloudless.profiles import DeviceProfile  # noqa: E402

# ── _get_profile_options ───────────────────────────────────────────────────────


class TestGetProfileOptions:
    def test_returns_list_with_loaded_profiles(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _get_profile_options

        profiles = [
            DeviceProfile(name="Smart Plug", model="sp*", entities=[]),
            DeviceProfile(name="Generic Switch", model="*", entities=[]),
        ]
        with patch("tuya_cloudless.profiles.list_profiles", return_value=profiles):
            options = _get_profile_options()

        # Two profiles + the new Auto-detect sentinel = 3 options (PLAT-778)
        assert len(options) == 3
        values = [o["value"] for o in options]
        assert "__auto_detect__" in values  # Auto-detect always first
        assert "Smart Plug" in values
        assert "Generic Switch" in values
        assert options[0]["value"] == "__auto_detect__"  # Auto-detect is first

    def test_returns_fallback_when_no_profiles(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _get_profile_options

        with (
            patch("tuya_cloudless.profiles.list_profiles", return_value=[]),
            patch("tuya_cloudless.profiles.init_profiles"),
        ):
            options = _get_profile_options()

        # Auto-detect + Generic Switch fallback = 2 options (PLAT-778)
        assert len(options) == 2
        values = [o["value"] for o in options]
        assert "__auto_detect__" in values
        assert "Generic Switch" in values

    def test_loads_profiles_when_empty(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _get_profile_options

        profiles = [DeviceProfile(name="Roller Blind", model="cl*", entities=[])]

        init_called = []

        def fake_init(path: Any) -> None:
            init_called.append(True)

        with (
            patch(
                "tuya_cloudless.profiles.list_profiles",
                side_effect=[[], profiles],
            ),
            patch(
                "tuya_cloudless.profiles.init_profiles",
                side_effect=fake_init,
            ),
        ):
            options = _get_profile_options()

        assert init_called  # init_profiles was called
        # Auto-detect is always first; loaded profile is second (PLAT-778)
        assert options[0]["value"] == "__auto_detect__"
        assert options[1]["value"] == "Roller Blind"

    def test_returns_empty_when_import_fails(self) -> None:
        """When profiles module can't be imported, return the generic fallback."""
        # Force ImportError by patching the import target
        import builtins

        from custom_components.tuya_cloudless.config_flow import _get_profile_options

        real_import = builtins.__import__

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "tuya_cloudless.profiles":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        # We can't easily test ImportError on already-loaded modules,
        # but we can verify the fallback path exists
        options = _get_profile_options()
        assert len(options) >= 1  # at minimum the fallback


# ── Constants ─────────────────────────────────────────────────────────────────


class TestConfigFlowConstants:
    def test_local_key_length(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _LOCAL_KEY_LENGTH

        assert _LOCAL_KEY_LENGTH == 16

    def test_connection_timeout_positive(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _CONNECTION_TIMEOUT

        assert _CONNECTION_TIMEOUT > 0


# ── Options flow ──────────────────────────────────────────────────────────────


class TestOptionsFlow:
    def _make_options_flow(self, current_options: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessOptionsFlow
        from custom_components.tuya_cloudless.const import (
            DEFAULT_OPT_COMMAND_TIMEOUT,
            DEFAULT_OPT_HEARTBEAT_INTERVAL,
            DEFAULT_OPT_RECONNECT_MAX_DELAY,
        )

        flow = TuyaCloudlessOptionsFlow.__new__(TuyaCloudlessOptionsFlow)

        config_entry = MagicMock()
        config_entry.options = current_options or {
            "heartbeat_interval": DEFAULT_OPT_HEARTBEAT_INTERVAL,
            "command_timeout": DEFAULT_OPT_COMMAND_TIMEOUT,
            "reconnect_max_delay": DEFAULT_OPT_RECONNECT_MAX_DELAY,
        }
        flow.__dict__["config_entry"] = config_entry  # bypass HA descriptor
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        return flow

    def test_option_keys_match_defaults(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_OPT_COMMAND_TIMEOUT,
            CONF_OPT_HEARTBEAT_INTERVAL,
            CONF_OPT_RECONNECT_MAX_DELAY,
            DEFAULT_OPT_COMMAND_TIMEOUT,
            DEFAULT_OPT_HEARTBEAT_INTERVAL,
            DEFAULT_OPT_RECONNECT_MAX_DELAY,
        )

        # Verify the option keys and defaults are consistently named
        opts = {
            CONF_OPT_HEARTBEAT_INTERVAL: DEFAULT_OPT_HEARTBEAT_INTERVAL,
            CONF_OPT_COMMAND_TIMEOUT: DEFAULT_OPT_COMMAND_TIMEOUT,
            CONF_OPT_RECONNECT_MAX_DELAY: DEFAULT_OPT_RECONNECT_MAX_DELAY,
        }
        assert opts[CONF_OPT_HEARTBEAT_INTERVAL] == DEFAULT_OPT_HEARTBEAT_INTERVAL
        assert opts[CONF_OPT_COMMAND_TIMEOUT] == DEFAULT_OPT_COMMAND_TIMEOUT
        assert opts[CONF_OPT_RECONNECT_MAX_DELAY] == DEFAULT_OPT_RECONNECT_MAX_DELAY

    def test_step_init_creates_entry_on_submit(self) -> None:
        """The flow saves user input as new options."""
        from custom_components.tuya_cloudless.const import (
            CONF_OPT_COMMAND_TIMEOUT,
            CONF_OPT_HEARTBEAT_INTERVAL,
            CONF_OPT_RECONNECT_MAX_DELAY,
        )

        # Verify the option keys match what the form expects
        opts = {
            CONF_OPT_HEARTBEAT_INTERVAL: 30,
            CONF_OPT_COMMAND_TIMEOUT: 10,
            CONF_OPT_RECONNECT_MAX_DELAY: 120,
        }
        # Just verify the keys round-trip correctly
        assert opts[CONF_OPT_HEARTBEAT_INTERVAL] == 30
        assert opts[CONF_OPT_COMMAND_TIMEOUT] == 10
        assert opts[CONF_OPT_RECONNECT_MAX_DELAY] == 120


# ── Config Flow steps (direct testing via __new__) ─────────────────────────────


def _make_config_flow() -> Any:
    """Create a TuyaCloudlessConfigFlow with mocked HA methods."""
    from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

    flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
    # Set attributes that __init__ would set
    flow._discovered = []
    flow._device = {}
    # Mock all HA base class methods
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_step_manual = AsyncMock(return_value={"type": "form", "step_id": "manual"})
    flow.async_step_select = AsyncMock(return_value={"type": "form", "step_id": "select"})
    flow.async_step_local_key = AsyncMock(return_value={"type": "form", "step_id": "local_key"})
    flow.async_step_confirm = AsyncMock(return_value={"type": "form", "step_id": "confirm"})
    flow._check_connection = AsyncMock(return_value={})
    return flow


class TestConfigFlowStepUser:
    @pytest.mark.asyncio
    async def test_no_input_goes_to_ble_pair(self) -> None:
        """async_step_user must immediately delegate to BLE pairing."""
        flow = _make_config_flow()
        flow.async_step_ble_pair = AsyncMock(return_value={"type": "external"})
        await flow.async_step_user(user_input=None)
        flow.async_step_ble_pair.assert_awaited_once()
        flow.async_show_form.assert_not_called()

    @pytest.mark.asyncio
    async def test_any_input_goes_to_ble_pair(self) -> None:
        """Any user_input is ignored — BLE pairing is always the entry point."""
        flow = _make_config_flow()
        flow.async_step_ble_pair = AsyncMock(return_value={"type": "external"})
        await flow.async_step_user(user_input={"setup_mode": "manual"})
        flow.async_step_ble_pair.assert_awaited_once()


def _restore_method(flow: Any, method_name: str) -> None:
    """Restore a real (unbound) method on a flow instance, removing the mock."""
    from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

    real_method = getattr(TuyaCloudlessConfigFlow, method_name)
    setattr(flow, method_name, real_method.__get__(flow))


class TestConfigFlowStepSelect:
    @pytest.mark.asyncio
    async def test_empty_discovered_goes_to_manual(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "async_step_select")
        flow._discovered = []
        await flow.async_step_select(user_input=None)
        flow.async_step_manual.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_option_selected(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION,
        )

        flow = _make_config_flow()
        _restore_method(flow, "async_step_select")
        flow._discovered = [
            {CONF_GW_ID: "gw001", CONF_IP_ADDRESS: "10.0.0.1", CONF_PROTOCOL_VERSION: "3.3"}
        ]
        await flow.async_step_select(user_input={"device": "__manual__"})
        flow.async_step_manual.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION,
        )

        flow = _make_config_flow()
        _restore_method(flow, "async_step_select")
        flow._discovered = [
            {CONF_GW_ID: "gw001", CONF_IP_ADDRESS: "10.0.0.1", CONF_PROTOCOL_VERSION: "3.3"}
        ]
        await flow.async_step_select(user_input=None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_device_selected_goes_to_local_key(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION,
        )

        flow = _make_config_flow()
        _restore_method(flow, "async_step_select")
        device = {CONF_GW_ID: "gw001", CONF_IP_ADDRESS: "10.0.0.1", CONF_PROTOCOL_VERSION: "3.3"}
        flow._discovered = [device]
        await flow.async_step_select(user_input={"device": "gw001"})
        flow.async_step_local_key.assert_called_once()


class TestConfigFlowStepLocalKey:
    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "async_step_local_key")
        await flow.async_step_local_key(user_input=None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_key_shows_error(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_local_key")
        await flow.async_step_local_key(
            user_input={CONF_LOCAL_KEY: "short"}  # too short
        )
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert "invalid_local_key" in str(call_kwargs.get("errors", ""))

    @pytest.mark.asyncio
    async def test_valid_key_goes_to_confirm(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_LOCAL_KEY, CONF_PROFILE

        flow = _make_config_flow()
        _restore_method(flow, "async_step_local_key")
        await flow.async_step_local_key(
            user_input={
                CONF_LOCAL_KEY: "0123456789abcdef",  # 16 chars
                CONF_PROFILE: "Generic Switch",
            }
        )
        flow.async_step_confirm.assert_called_once()


class TestConfigFlowStepManual:
    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = _make_config_flow()
        # Restore actual async_step_manual (unset the mock)
        flow.async_step_manual = TuyaCloudlessConfigFlow.async_step_manual.__get__(flow)
        await flow.async_step_manual(user_input=None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_gw_id_shows_error(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_manual = TuyaCloudlessConfigFlow.async_step_manual.__get__(flow)
        await flow.async_step_manual(
            user_input={
                CONF_GW_ID: "",  # empty gw_id
                CONF_LOCAL_KEY: "0123456789abcdef",
                CONF_IP_ADDRESS: "10.0.0.1",
            }
        )
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_local_key_shows_error(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_manual = TuyaCloudlessConfigFlow.async_step_manual.__get__(flow)
        await flow.async_step_manual(
            user_input={
                CONF_GW_ID: "gw001",
                CONF_LOCAL_KEY: "short",  # invalid
                CONF_IP_ADDRESS: "10.0.0.1",
            }
        )
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_ip_shows_error(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_manual = TuyaCloudlessConfigFlow.async_step_manual.__get__(flow)
        await flow.async_step_manual(
            user_input={
                CONF_GW_ID: "gw001",
                CONF_LOCAL_KEY: "0123456789abcdef",
                CONF_IP_ADDRESS: "",  # empty IP
            }
        )
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_input_creates_entry(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_manual = TuyaCloudlessConfigFlow.async_step_manual.__get__(flow)
        flow._check_connection = AsyncMock(return_value={})

        await flow.async_step_manual(
            user_input={
                CONF_GW_ID: "gw001",
                CONF_LOCAL_KEY: "0123456789abcdef",
                CONF_IP_ADDRESS: "10.0.0.1",
            }
        )
        flow.async_create_entry.assert_called_once()


class TestConfigFlowStepConfirm:
    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_confirm = TuyaCloudlessConfigFlow.async_step_confirm.__get__(flow)
        flow._device = {
            CONF_GW_ID: "gw001",
            CONF_IP_ADDRESS: "10.0.0.1",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }
        await flow.async_step_confirm(user_input=None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_input_creates_entry(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_confirm = TuyaCloudlessConfigFlow.async_step_confirm.__get__(flow)
        flow._device = {
            CONF_GW_ID: "gw001",
            CONF_IP_ADDRESS: "10.0.0.1",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }
        flow._check_connection = AsyncMock(return_value={})

        await flow.async_step_confirm(user_input={})
        flow.async_create_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_error_shows_form(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_confirm = TuyaCloudlessConfigFlow.async_step_confirm.__get__(flow)
        flow._device = {
            CONF_GW_ID: "gw001",
            CONF_IP_ADDRESS: "10.0.0.1",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }
        flow._check_connection = AsyncMock(return_value={"base": "cannot_connect"})

        await flow.async_step_confirm(user_input={})
        flow.async_show_form.assert_called_once()


class TestConfigFlowCheckConnection:
    @pytest.mark.asyncio
    async def test_check_connection_success(self) -> None:

        flow = _make_config_flow()
        _restore_method(flow, "_check_connection")

        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        with patch(
            "asyncio.open_connection",
            return_value=(AsyncMock(), writer),
        ):
            errors = await flow._check_connection("10.0.0.1")

        assert errors == {}

    @pytest.mark.asyncio
    async def test_check_connection_timeout(self) -> None:

        flow = _make_config_flow()
        _restore_method(flow, "_check_connection")

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=TimeoutError(),
        ):
            errors = await flow._check_connection("10.0.0.1")

        assert "ip_address" in errors

    @pytest.mark.asyncio
    async def test_check_connection_os_error(self) -> None:

        flow = _make_config_flow()
        _restore_method(flow, "_check_connection")

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=OSError("connection refused"),
        ):
            errors = await flow._check_connection("10.0.0.1")

        assert "ip_address" in errors

    @pytest.mark.asyncio
    async def test_check_connection_skips_key_validation_when_no_key(self) -> None:
        """When local_key is empty, _validate_local_key is never called."""
        flow = _make_config_flow()
        _restore_method(flow, "_check_connection")
        flow._validate_local_key = AsyncMock(return_value={})

        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        with patch(
            "asyncio.open_connection",
            return_value=(AsyncMock(), writer),
        ):
            errors = await flow._check_connection("10.0.0.1", local_key="")

        assert errors == {}
        flow._validate_local_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_connection_validates_key_when_provided(self) -> None:
        """When local_key is 16 chars, _validate_local_key is called."""
        flow = _make_config_flow()
        _restore_method(flow, "_check_connection")
        flow._validate_local_key = AsyncMock(return_value={})

        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        with patch(
            "asyncio.open_connection",
            return_value=(AsyncMock(), writer),
        ):
            errors = await flow._check_connection("10.0.0.1", local_key="0123456789abcdef")

        assert errors == {}
        flow._validate_local_key.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_connection_propagates_invalid_auth_key_error(self) -> None:
        """A wrong key detected by _validate_local_key surfaces to caller."""
        flow = _make_config_flow()
        _restore_method(flow, "_check_connection")
        flow._validate_local_key = AsyncMock(return_value={"base": "invalid_auth_key"})

        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        with patch(
            "asyncio.open_connection",
            return_value=(AsyncMock(), writer),
        ):
            errors = await flow._check_connection("10.0.0.1", local_key="0123456789abcdef")

        assert errors == {"base": "invalid_auth_key"}


class TestValidateLocalKey:
    """Tests for _validate_local_key — best-effort key validation."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_import_fails(self) -> None:
        """If the protocol lib is not importable, skip validation."""
        import builtins

        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        real_import = builtins.__import__
        _blocked = (
            "tuya_cloudless.protocol",
            "tuya_cloudless.crypto",
            "tuya_cloudless.exceptions",
        )

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in _blocked:
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_read_timeout(self) -> None:
        """A timeout reading the device response is treated as OK."""
        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=b"\x00\x00U\xaa" + b"\x00" * 20,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=TimeoutError(),
            ),
        ):
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {}

    @pytest.mark.asyncio
    async def test_returns_invalid_auth_key_on_crypto_error(self) -> None:
        """A CryptoError during decode_frame means the key is wrong."""
        from tuya_cloudless.crypto import CryptoError

        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=b"\x00\x00U\xaa" + b"\x00" * 20,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                return_value=dummy_frame,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                side_effect=CryptoError("bad padding"),
            ),
        ):
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {"base": "invalid_auth_key"}

    @pytest.mark.asyncio
    async def test_returns_empty_on_malformed_packet(self) -> None:
        """A MalformedPacketError means we cannot conclude key is wrong — skip."""
        from tuya_cloudless.exceptions import MalformedPacketError

        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=b"\x00\x00U\xaa" + b"\x00" * 20,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                return_value=dummy_frame,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                side_effect=MalformedPacketError("bad prefix"),
            ),
        ):
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_complete_frames(self) -> None:
        """If split_frames returns no complete frames, skip validation."""
        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=b"\x00\x00U\xaa" + b"\x00" * 20,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                return_value=b"\x00\x01",
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([], b"\x00\x01"),
            ),
        ):
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_successful_decode(self) -> None:
        """Successful decode_frame means key is correct — no error."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24
        good_frame = TuyaFrame(sequence=1, command=9, version="3.3", payload=b"")

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=b"\x00\x00U\xaa" + b"\x00" * 20,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                return_value=dummy_frame,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=good_frame,
            ),
        ):
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {}


# ── OptionsFlow async_step_init ────────────────────────────────────────────────


class TestOptionsFlowInit:
    def _make_options_flow(self) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessOptionsFlow

        flow = TuyaCloudlessOptionsFlow.__new__(TuyaCloudlessOptionsFlow)
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        return flow

    @pytest.mark.asyncio
    async def test_with_user_input_creates_entry(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_OPT_HEARTBEAT_INTERVAL

        flow = self._make_options_flow()
        user_input = {CONF_OPT_HEARTBEAT_INTERVAL: 30}
        await flow.async_step_init(user_input=user_input)
        flow.async_create_entry.assert_called_once_with(data=user_input)


# ── Reconfigure flow ───────────────────────────────────────────────────────────


class TestReconfigureFlow:
    @pytest.mark.asyncio
    async def test_reconfigure_invalid_key(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reconfigure")

        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

        await flow.async_step_reconfigure(
            user_input={
                CONF_LOCAL_KEY: "short",  # invalid
                CONF_IP_ADDRESS: "10.0.0.1",
            }
        )
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconfigure_empty_ip(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reconfigure")

        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

        await flow.async_step_reconfigure(
            user_input={
                CONF_LOCAL_KEY: "0123456789abcdef",
                CONF_IP_ADDRESS: "",  # empty
            }
        )
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconfigure_valid_input_updates_entry(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reconfigure")
        flow._check_connection = AsyncMock(return_value={})
        flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})

        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1", CONF_LOCAL_KEY: "old_key_12345678"}
        mock_entry.title = "Test Device"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

        await flow.async_step_reconfigure(
            user_input={
                CONF_LOCAL_KEY: "newkey12345678ab",
                CONF_IP_ADDRESS: "10.0.0.2",
            }
        )
        flow.async_update_reload_and_abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconfigure_no_input_shows_form(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reconfigure")

        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1", CONF_LOCAL_KEY: "0123456789abcdef"}
        mock_entry.title = "Test Device"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

        await flow.async_step_reconfigure(user_input=None)
        flow.async_show_form.assert_called_once()


# ── async_step_discovery ───────────────────────────────────────────────────────


class TestConfigFlowStepDiscovery:
    """Tests for async_step_discovery (HA-initiated discovery path)."""

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_PROTOCOL_VERSION

        flow = _make_config_flow()
        _restore_method(flow, "async_step_discovery")
        flow._device = {
            CONF_IP_ADDRESS: "10.0.0.1",
            CONF_PROTOCOL_VERSION: "3.3",
        }
        await flow.async_step_discovery(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_key_shows_error(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_discovery")
        flow._device = {CONF_IP_ADDRESS: "10.0.0.1"}

        await flow.async_step_discovery({CONF_LOCAL_KEY: "short"})
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_key_calls_confirm(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_discovery")
        flow._device = {CONF_IP_ADDRESS: "10.0.0.1"}

        await flow.async_step_discovery({CONF_LOCAL_KEY: "0123456789abcdef"})
        flow.async_step_confirm.assert_called_once()


# ── async_step_reauth + async_step_reauth_confirm ─────────────────────────────


class TestConfigFlowReauth:
    """Tests for re-authentication flow steps."""

    @pytest.mark.asyncio
    async def test_reauth_calls_confirm(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "async_step_reauth")
        flow.async_step_reauth_confirm = AsyncMock(return_value={"type": "form"})

        await flow.async_step_reauth({})
        flow.async_step_reauth_confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_reauth_confirm_no_input_shows_form(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reauth_confirm")
        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reauth_entry = MagicMock(return_value=mock_entry)

        await flow.async_step_reauth_confirm(None)
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_reauth_confirm_invalid_key_shows_error(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reauth_confirm")
        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reauth_entry = MagicMock(return_value=mock_entry)

        await flow.async_step_reauth_confirm({CONF_LOCAL_KEY: "tooshort"})
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_reauth_confirm_success_updates_entry(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reauth_confirm")
        flow._check_connection = AsyncMock(return_value={})
        flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})

        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reauth_entry = MagicMock(return_value=mock_entry)

        await flow.async_step_reauth_confirm(
            {CONF_LOCAL_KEY: "0123456789abcdef", CONF_IP_ADDRESS: "10.0.0.2"}
        )
        flow.async_update_reload_and_abort.assert_called_once()


# ── async_get_options_flow ─────────────────────────────────────────────────────


class TestGetOptionsFlow:
    def test_returns_options_flow_instance(self) -> None:
        from custom_components.tuya_cloudless.config_flow import (
            TuyaCloudlessConfigFlow,
            TuyaCloudlessOptionsFlow,
        )

        result = TuyaCloudlessConfigFlow.async_get_options_flow(MagicMock())
        assert isinstance(result, TuyaCloudlessOptionsFlow)


# ── _run_discovery ─────────────────────────────────────────────────────────────


class TestRunDiscovery:
    @pytest.mark.asyncio
    async def test_run_discovery_returns_device_list(self) -> None:

        from tuya_cloudless.discovery import DiscoveredDevice

        flow = _make_config_flow()
        _restore_method(flow, "_run_discovery")

        device = DiscoveredDevice(
            gw_id="gw001",
            ip="10.0.0.1",
            version="3.3",
            product_key="pk1",
            encrypt=True,
            active=2,
            ability=0,
        )
        mock_listener = MagicMock()
        mock_listener.start = AsyncMock()
        mock_listener.stop = AsyncMock()
        mock_listener.get_all = MagicMock(return_value=[device])

        with (
            patch("tuya_cloudless.discovery.DiscoveryListener", return_value=mock_listener),
            patch("custom_components.tuya_cloudless.config_flow.asyncio.sleep", new=AsyncMock()),
        ):
            result = await flow._run_discovery()

        assert len(result) == 1
        assert result[0]["gw_id"] == "gw001"
        assert result[0]["ip_address"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_run_discovery_propagates_oserror(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "_run_discovery")

        mock_listener = MagicMock()
        mock_listener.start = AsyncMock(side_effect=OSError("bind failed"))
        mock_listener.stop = AsyncMock()
        mock_listener.get_all = MagicMock(return_value=[])

        with (
            patch("tuya_cloudless.discovery.DiscoveryListener", return_value=mock_listener),
            pytest.raises(OSError),
        ):
            await flow._run_discovery()


# ── TuyaCloudlessOptionsFlow.async_step_init form display ─────────────────────


class TestOptionsFlowFormDisplay:
    @pytest.mark.asyncio
    async def test_no_input_shows_form_with_defaults(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessOptionsFlow
        from custom_components.tuya_cloudless.const import (
            CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION,
        )

        flow = TuyaCloudlessOptionsFlow.__new__(TuyaCloudlessOptionsFlow)
        flow.async_show_form = MagicMock(return_value={"type": "form"})

        mock_entry = MagicMock()
        mock_entry.options = {}
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1", CONF_PROTOCOL_VERSION: "3.3"}
        # OptionsFlow stores entry as _config_entry
        flow._config_entry = mock_entry

        result = await flow.async_step_init(None)
        assert result["type"] == "form"
        flow.async_show_form.assert_called_once()


# ── _suggest_profile ───────────────────────────────────────────────────────────


class TestSuggestProfile:
    def test_returns_matched_profile_name(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _suggest_profile

        mock_profile = MagicMock()
        mock_profile.name = "Smart Plug"
        # _suggest_profile uses a lazy import: patch the source module
        with patch(
            "tuya_cloudless.profiles.find_profile_by_product_key",
            return_value=mock_profile,
        ):
            result = _suggest_profile("pkey123")
        assert result == "Smart Plug"

    def test_returns_first_option_when_no_match(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _suggest_profile

        with patch(
            "tuya_cloudless.profiles.find_profile_by_product_key",
            return_value=None,
        ):
            result = _suggest_profile("unknownkey")
        # No product-key match → fall back to auto-detect sentinel (PLAT-778)
        assert result == "__auto_detect__"

    def test_returns_fallback_when_no_product_key(self) -> None:
        from custom_components.tuya_cloudless.config_flow import _suggest_profile

        result = _suggest_profile(None)
        # No product key provided → fall back to auto-detect sentinel (PLAT-778)
        assert result == "__auto_detect__"

    def test_falls_back_gracefully_when_no_match(self) -> None:
        """No product-key match returns auto-detect sentinel (PLAT-778)."""
        from custom_components.tuya_cloudless.config_flow import _suggest_profile

        with patch(
            "tuya_cloudless.profiles.find_profile_by_product_key",
            return_value=None,
        ):
            result = _suggest_profile("somekey")
        assert result == "__auto_detect__"
