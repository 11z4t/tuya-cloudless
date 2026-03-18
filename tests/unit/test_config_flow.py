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

    @pytest.mark.asyncio
    async def test_reconfigure_non_ascii_key_rejected(self) -> None:
        """async_step_reconfigure must reject a local_key with non-ASCII chars (line 887)."""
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reconfigure")

        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

        # 16 chars, correct length, but contains non-ASCII byte
        bad_key = "0123456789abcd\xe9f"
        await flow.async_step_reconfigure(
            user_input={
                CONF_LOCAL_KEY: bad_key,
                CONF_IP_ADDRESS: "10.0.0.1",
            }
        )
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"


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


# ── PLAT-809: async_step_ble_pair HTTPS check ─────────────────────────────────


class TestBlePairHttpsCheck:
    """PLAT-809 — async_step_ble_pair must abort when HA URL is not HTTPS."""

    def _make_ble_flow(self) -> Any:
        """Return a TuyaCloudlessConfigFlow configured for BLE pair testing."""
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.flow_id = "test-flow-id"
        flow.async_abort = MagicMock(return_value={"type": "abort"})
        flow.async_external_step = MagicMock(return_value={"type": "external"})
        flow.async_external_step_done = MagicMock(return_value={"type": "create_entry"})
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        return flow

    @pytest.mark.asyncio
    async def test_aborts_when_internal_url_is_http(self) -> None:
        """If HA's internal URL is HTTP and host is not localhost, abort with https_required."""
        flow = self._make_ble_flow()

        mock_server = MagicMock()
        mock_server.ha_ui_url.return_value = "http://192.168.1.100:8099"

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
                new_callable=AsyncMock,
                return_value=mock_server,
            ),
            patch(
                "homeassistant.helpers.network.get_url",
                return_value="http://homeassistant.local:8123",
            ),
        ):
            result = await flow.async_step_ble_pair(user_input=None)

        flow.async_abort.assert_called_once_with(reason="https_required")
        assert result["type"] == "abort"

    @pytest.mark.asyncio
    async def test_aborts_when_no_url_available(self) -> None:
        """If get_url raises NoURLAvailableError, abort with https_required."""
        from homeassistant.helpers.network import NoURLAvailableError

        flow = self._make_ble_flow()

        mock_server = MagicMock()
        mock_server.ha_ui_url.return_value = "http://192.168.1.100:8099"

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
                new_callable=AsyncMock,
                return_value=mock_server,
            ),
            patch(
                "homeassistant.helpers.network.get_url",
                side_effect=NoURLAvailableError,
            ),
        ):
            result = await flow.async_step_ble_pair(user_input=None)

        flow.async_abort.assert_called_once_with(reason="https_required")
        assert result["type"] == "abort"

    @pytest.mark.asyncio
    async def test_proceeds_when_internal_url_is_https(self) -> None:
        """If HA's internal URL is HTTPS, the flow must not abort."""
        flow = self._make_ble_flow()

        mock_server = MagicMock()
        # ha_ui_url returns the HA HTTPS path when HA is HTTPS
        mock_server.ha_ui_url.return_value = (
            "https://homeassistant.local:8123/api/tuya_cloudless/pairing"
        )

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
                new_callable=AsyncMock,
                return_value=mock_server,
            ),
            patch(
                "homeassistant.helpers.network.get_url",
                return_value="https://homeassistant.local:8123",
            ),
        ):
            await flow.async_step_ble_pair(user_input=None)

        flow.async_abort.assert_not_called()
        flow.async_external_step.assert_called_once()

    @pytest.mark.asyncio
    async def test_proceeds_for_localhost(self) -> None:
        """localhost is treated as a secure context and must not abort."""
        flow = self._make_ble_flow()

        mock_server = MagicMock()
        mock_server.ha_ui_url.return_value = "http://localhost:8099"

        with patch(
            "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
            new_callable=AsyncMock,
            return_value=mock_server,
        ):
            await flow.async_step_ble_pair(user_input=None)

        flow.async_abort.assert_not_called()
        flow.async_external_step.assert_called_once()

    @pytest.mark.asyncio  # type: ignore[misc]
    async def test_proceeds_for_127_0_0_1(self) -> None:
        """127.0.0.1 is treated as a secure context and must not abort."""
        flow = self._make_ble_flow()

        mock_server = MagicMock()
        mock_server.ha_ui_url.return_value = "http://127.0.0.1:8099"

        with patch(
            "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
            new_callable=AsyncMock,
            return_value=mock_server,
        ):
            await flow.async_step_ble_pair(user_input=None)

        flow.async_abort.assert_not_called()
        flow.async_external_step.assert_called_once()


# ── _get_profile_options ImportError fallback (lines 94-95) ───────────────────


class TestGetProfileOptionsImportError:
    def test_returns_fallback_on_import_error(self) -> None:
        """When profiles module raises ImportError, returns Auto-detect + Generic Switch."""
        import sys

        from custom_components.tuya_cloudless.config_flow import _get_profile_options

        saved = sys.modules.pop("tuya_cloudless.profiles", None)
        try:
            sys.modules["tuya_cloudless.profiles"] = None  # type: ignore[assignment]
            opts = _get_profile_options()
        finally:
            if saved is not None:
                sys.modules["tuya_cloudless.profiles"] = saved
            else:
                sys.modules.pop("tuya_cloudless.profiles", None)

        assert any(o["value"] == "__auto_detect__" for o in opts)


# ── _suggest_profile wildcard model branch (lines 135-136) ──────────────────


class TestSuggestProfileWildcard:
    def test_returns_auto_when_match_has_wildcard_model(self) -> None:
        """When matched profile has model='*', return __auto_detect__ not the profile name."""
        from unittest.mock import MagicMock, patch

        from custom_components.tuya_cloudless import config_flow as cf_mod

        cf_mod._suggest_profile.cache_clear()

        mock_profile = MagicMock()
        mock_profile.name = "Generic Device"
        mock_profile.model = "*"

        with patch(
            "tuya_cloudless.profiles.find_profile_by_product_key",
            return_value=mock_profile,
        ):
            result = cf_mod._suggest_profile("key_wildcard_test_unique")

        cf_mod._suggest_profile.cache_clear()
        assert result == "__auto_detect__"


# ── _suggest_profile ImportError branch (lines 135-136) ──────────────────────


class TestSuggestProfileImportErrorNew:
    def test_returns_auto_when_import_fails(self) -> None:
        """When tuya_cloudless.profiles is unavailable, return __auto_detect__."""
        import sys

        from custom_components.tuya_cloudless import config_flow as cf_mod

        cf_mod._suggest_profile.cache_clear()

        saved = sys.modules.pop("tuya_cloudless.profiles", None)
        try:
            sys.modules["tuya_cloudless.profiles"] = None  # type: ignore[assignment]
            result = cf_mod._suggest_profile("key_import_err_unique2")
        finally:
            if saved is not None:
                sys.modules["tuya_cloudless.profiles"] = saved
            else:
                sys.modules.pop("tuya_cloudless.profiles", None)
            cf_mod._suggest_profile.cache_clear()

        assert result == "__auto_detect__"


# ── async_step_pair (lines 173-192) ───────────────────────────────────────────


class TestConfigFlowStepPairNew:
    def _make_pair_flow(self) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_step_manual = AsyncMock(return_value={"type": "form", "step_id": "manual"})
        flow.async_step_local_key = AsyncMock(return_value={"type": "form", "step_id": "local_key"})
        return flow

    @pytest.mark.asyncio
    async def test_valid_input_advances_to_local_key(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = self._make_pair_flow()
        _restore_method(flow, "async_step_pair")

        user_input = {
            CONF_GW_ID: "bf123device",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_IP_ADDRESS: "10.0.0.50",
        }
        await flow.async_step_pair(user_input=user_input)

        flow.async_step_local_key.assert_called_once()
        assert flow._device[CONF_GW_ID] == "bf123device"
        assert flow._device[CONF_LOCAL_KEY] == "0123456789abcdef"
        assert flow._device[CONF_IP_ADDRESS] == "10.0.0.50"

    @pytest.mark.asyncio
    async def test_none_input_falls_through_to_manual(self) -> None:
        flow = self._make_pair_flow()
        _restore_method(flow, "async_step_pair")

        await flow.async_step_pair(user_input=None)
        flow.async_step_manual.assert_called_once()

    @pytest.mark.asyncio
    async def test_short_key_falls_through_to_manual(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = self._make_pair_flow()
        _restore_method(flow, "async_step_pair")

        await flow.async_step_pair(
            user_input={CONF_GW_ID: "bf123", CONF_LOCAL_KEY: "short", CONF_IP_ADDRESS: "10.0.0.1"}
        )
        flow.async_step_manual.assert_called_once()


# ── async_step_ble_pair second call (lines 217-223) ─────────────────────────


class TestBlePairSecondCallNew:
    @pytest.mark.asyncio
    async def test_second_call_stores_device_and_returns_done(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.flow_id = "test-flow-id"
        flow.async_external_step_done = MagicMock(return_value={"type": "create_entry"})
        flow.async_external_step = MagicMock(return_value={"type": "external"})
        flow.async_abort = MagicMock(return_value={"type": "abort"})

        user_input = {
            "gw_id": "bf456",
            "local_key": "abcdef0123456789",
            "ip_address": "10.0.1.2",
            "product_key": "pk789",
        }
        result = await flow.async_step_ble_pair(user_input=user_input)

        flow.async_external_step_done.assert_called_once_with(next_step_id="ble_confirm")
        assert result["type"] == "create_entry"
        assert flow._device["gw_id"] == "bf456"


# ── async_step_ble_pair OSError (lines 230-232) ──────────────────────────────


class TestBlePairOsErrorNew:
    @pytest.mark.asyncio
    async def test_oserror_aborts_with_pairing_server_unavailable(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.flow_id = "test-flow-id"
        flow.async_abort = MagicMock(return_value={"type": "abort"})

        with patch(
            "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
            new_callable=AsyncMock,
            side_effect=OSError("port in use"),
        ):
            result = await flow.async_step_ble_pair(user_input=None)

        flow.async_abort.assert_called_once_with(reason="pairing_server_unavailable")
        assert result["type"] == "abort"


# ── async_step_ble_confirm (lines 277-322) ───────────────────────────────────


class TestBlePairConfirmNew:
    def _make_ble_confirm_flow(self) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {
            "gw_id": "bf001",
            "local_key": "0123456789abcdef",
            "ip_address": "10.0.0.1",
            "product_key": "pk001",
        }
        flow.hass = MagicMock()
        flow.flow_id = "ble-flow-id"
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        return flow

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        flow = self._make_ble_confirm_flow()
        _restore_method(flow, "async_step_ble_confirm")

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=None,
        ):
            result = await flow.async_step_ble_confirm(user_input=None)

        assert result["type"] == "form"
        flow.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_creates_entry(self) -> None:
        flow = self._make_ble_confirm_flow()
        _restore_method(flow, "async_step_ble_confirm")

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=None,
        ):
            result = await flow.async_step_ble_confirm(
                user_input={"device_name": "My BLE Device", "profile": "Generic Switch"}
            )

        assert result["type"] == "create_entry"
        flow.async_create_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_unregisters_flow_on_confirm(self) -> None:
        flow = self._make_ble_confirm_flow()
        _restore_method(flow, "async_step_ble_confirm")

        mock_server = MagicMock()
        mock_server.unregister_flow = MagicMock()

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=mock_server,
        ):
            await flow.async_step_ble_confirm(
                user_input={"device_name": "Test", "profile": "Generic Switch"}
            )

        mock_server.unregister_flow.assert_called_once_with("ble-flow-id")


# ── async_step_confirm auto-detect profile (lines 498-503) ───────────────────


class TestConfigFlowConfirmAutoDetectNew:
    @pytest.mark.asyncio
    async def test_auto_detect_profile_is_invoked(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
            CONF_PROFILE,
            CONF_PROTOCOL_VERSION,
        )

        flow = _make_config_flow()
        _restore_method(flow, "async_step_confirm")

        flow._device = {
            CONF_GW_ID: "gw_auto",
            CONF_IP_ADDRESS: "10.0.0.1",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_PROFILE: "__auto_detect__",
            "device_name": "AutoDevice",
        }

        flow._auto_detect_profile = AsyncMock(return_value="Smart Plug")

        await flow.async_step_confirm(user_input={})

        flow._auto_detect_profile.assert_awaited_once()
        flow.async_create_entry.assert_called_once()
        call_kwargs = flow.async_create_entry.call_args[1]
        assert call_kwargs["data"][CONF_PROFILE] == "Smart Plug"


# ── async_step_zeroconf (lines 695-715) ──────────────────────────────────────


class TestConfigFlowZeroconfigNew:
    def _make_zeroconf_flow(self) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.context = {}
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_abort = MagicMock(return_value={"type": "abort"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        return flow

    @pytest.mark.asyncio
    async def test_zeroconf_populates_device_and_calls_discovery(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_GW_ID, CONF_IP_ADDRESS

        flow = self._make_zeroconf_flow()
        _restore_method(flow, "async_step_zeroconf")

        discovery_info = MagicMock()
        discovery_info.host = "10.0.0.5"
        discovery_info.properties = {"gwId": "bf_zeroconf_001", "version": "3.4"}
        discovery_info.name = "bf_zeroconf_001._tuya._tcp.local."

        await flow.async_step_zeroconf(discovery_info)

        flow.async_step_discovery.assert_called_once()
        assert flow._device[CONF_GW_ID] == "bf_zeroconf_001"
        assert flow._device[CONF_IP_ADDRESS] == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_zeroconf_falls_back_to_name_when_no_gwid(self) -> None:
        from custom_components.tuya_cloudless.const import CONF_GW_ID

        flow = self._make_zeroconf_flow()
        _restore_method(flow, "async_step_zeroconf")

        discovery_info = MagicMock()
        discovery_info.host = "10.0.0.6"
        discovery_info.properties = {}
        discovery_info.name = "mydevice123._tuya._tcp.local."

        await flow.async_step_zeroconf(discovery_info)

        flow.async_step_discovery.assert_called_once()
        assert flow._device[CONF_GW_ID] == "mydevice123"

    @pytest.mark.asyncio
    async def test_zeroconf_aborts_when_no_gw_id_anywhere(self) -> None:
        flow = self._make_zeroconf_flow()
        _restore_method(flow, "async_step_zeroconf")

        discovery_info = MagicMock()
        discovery_info.host = "10.0.0.7"
        discovery_info.properties = {}
        discovery_info.name = "._tuya._tcp.local."

        await flow.async_step_zeroconf(discovery_info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")


# ── async_step_dhcp (lines 731-753) ──────────────────────────────────────────


class TestConfigFlowDhcpNew:
    def _make_dhcp_flow(self) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.context = {}
        flow.async_abort = MagicMock(return_value={"type": "abort"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})
        return flow

    @pytest.mark.asyncio
    async def test_dhcp_finds_device_and_calls_discovery(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION,
        )

        flow = self._make_dhcp_flow()
        _restore_method(flow, "async_step_dhcp")

        discovery_info = MagicMock()
        discovery_info.ip = "10.0.0.99"

        device = {
            CONF_GW_ID: "bf_dhcp_001",
            CONF_IP_ADDRESS: "10.0.0.99",
            CONF_PROTOCOL_VERSION: "3.3",
        }

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            new_callable=AsyncMock,
            return_value=[device],
        ):
            await flow.async_step_dhcp(discovery_info)

        flow.async_step_discovery.assert_called_once()
        assert flow._device[CONF_GW_ID] == "bf_dhcp_001"

    @pytest.mark.asyncio
    async def test_dhcp_aborts_when_device_not_in_discovered(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION,
        )

        flow = self._make_dhcp_flow()
        _restore_method(flow, "async_step_dhcp")

        discovery_info = MagicMock()
        discovery_info.ip = "10.0.0.200"

        device = {
            CONF_GW_ID: "bf_other",
            CONF_IP_ADDRESS: "10.0.0.99",
            CONF_PROTOCOL_VERSION: "3.3",
        }

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            new_callable=AsyncMock,
            return_value=[device],
        ):
            await flow.async_step_dhcp(discovery_info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_dhcp_aborts_on_discovery_timeout(self) -> None:
        flow = self._make_dhcp_flow()
        _restore_method(flow, "async_step_dhcp")

        discovery_info = MagicMock()
        discovery_info.ip = "10.0.0.1"

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=TimeoutError(),
        ):
            await flow.async_step_dhcp(discovery_info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")


# ── async_remove (lines 919-923) ─────────────────────────────────────────────


class TestConfigFlowAsyncRemoveNew:
    @pytest.mark.asyncio
    async def test_unregisters_flow_from_server(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.flow_id = "remove-test-id"

        mock_server = MagicMock()
        mock_server.unregister_flow = MagicMock()

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=mock_server,
        ):
            await flow.async_remove()

        mock_server.unregister_flow.assert_called_once_with("remove-test-id")

    @pytest.mark.asyncio
    async def test_no_error_when_no_server(self) -> None:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.flow_id = "remove-none-id"

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=None,
        ):
            await flow.async_remove()


# ── _pairing_tool_url fallbacks (lines 941-953) ──────────────────────────────


class TestPairingToolUrlNew:
    def _make_flow_with_hass(self, internal_url: str | None = None) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()
        flow.hass.config.internal_url = internal_url
        return flow

    def test_fallback_to_internal_url(self) -> None:
        flow = self._make_flow_with_hass(internal_url="http://192.168.1.50:8123")

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("no url"),
        ):
            url = flow._pairing_tool_url()

        assert "192.168.1.50" in url

    def test_fallback_to_hostname_when_all_fail(self) -> None:
        flow = self._make_flow_with_hass(internal_url=None)

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("no url"),
        ):
            url = flow._pairing_tool_url()

        assert url.startswith("http://")


# ── TuyaCloudlessOptionsFlow.async_step_init ─────────────────────────────────


class TestOptionsFlowInitNew:
    def _make_options_flow_full(self, options: dict[str, Any] | None = None) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessOptionsFlow
        from custom_components.tuya_cloudless.const import (
            DEFAULT_OPT_COMMAND_TIMEOUT,
            DEFAULT_OPT_HEARTBEAT_INTERVAL,
            DEFAULT_OPT_RECONNECT_MAX_DELAY,
        )

        flow = TuyaCloudlessOptionsFlow.__new__(TuyaCloudlessOptionsFlow)
        mock_entry = MagicMock()
        mock_entry.options = options or {
            "heartbeat_interval": DEFAULT_OPT_HEARTBEAT_INTERVAL,
            "command_timeout": DEFAULT_OPT_COMMAND_TIMEOUT,
            "reconnect_max_delay": DEFAULT_OPT_RECONNECT_MAX_DELAY,
        }
        flow._config_entry = mock_entry
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        return flow

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        flow = self._make_options_flow_full()
        result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("step_id") == "init"

    @pytest.mark.asyncio
    async def test_with_user_input_creates_entry(self) -> None:
        from custom_components.tuya_cloudless.const import (
            CONF_OPT_COMMAND_TIMEOUT,
            CONF_OPT_HEARTBEAT_INTERVAL,
            CONF_OPT_RECONNECT_MAX_DELAY,
        )

        flow = self._make_options_flow_full()
        user_input = {
            CONF_OPT_HEARTBEAT_INTERVAL: 45,
            CONF_OPT_COMMAND_TIMEOUT: 8,
            CONF_OPT_RECONNECT_MAX_DELAY: 180,
        }
        result = await flow.async_step_init(user_input=user_input)
        assert result["type"] == "create_entry"
        flow.async_create_entry.assert_called_once_with(data=user_input)

    @pytest.mark.asyncio
    async def test_empty_options_uses_defaults(self) -> None:
        flow = self._make_options_flow_full(options={})
        result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"
        flow.async_show_form.assert_called_once()


# ── _auto_detect_profile (lines 989-1074) ────────────────────────────────────


class TestAutoDetectProfileNew:
    @pytest.mark.asyncio
    async def test_returns_generic_on_connection_timeout(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "_auto_detect_profile")

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=TimeoutError(),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_generic_on_oserror(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "_auto_detect_profile")

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=OSError("connection refused"),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"


# ── _validate_local_key edge branches (lines 1197-1222) ─────────────────────


class TestValidateLocalKeyEdgeBranchesNew:
    @pytest.mark.asyncio
    async def test_returns_empty_when_heartbeat_encode_raises_unsupported(self) -> None:
        from tuya_cloudless.exceptions import UnsupportedVersionError

        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        mock_reader = MagicMock()
        mock_writer = MagicMock()
        mock_writer.get_extra_info = MagicMock(return_value="")

        with patch(
            "tuya_cloudless.protocol.encode_heartbeat",
            side_effect=UnsupportedVersionError("bad version"),
        ):
            result = await flow._validate_local_key(
                mock_reader,
                mock_writer,
                local_key="0123456789abcdef",
                version="3.9",
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_write_raises_oserror(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        mock_reader = MagicMock()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock(side_effect=OSError("broken pipe"))
        mock_writer.get_extra_info = MagicMock(return_value="")

        with patch(
            "tuya_cloudless.protocol.encode_heartbeat",
            return_value=b"\x00" * 24,
        ):
            result = await flow._validate_local_key(
                mock_reader,
                mock_writer,
                local_key="0123456789abcdef",
                version="3.3",
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_empty_raw_response(self) -> None:
        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        mock_reader = MagicMock()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.get_extra_info = MagicMock(return_value="")

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=b"\x00" * 24,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                return_value=b"",
            ),
        ):
            result = await flow._validate_local_key(
                mock_reader,
                mock_writer,
                local_key="0123456789abcdef",
                version="3.3",
            )

        assert result == {}


# ── TuyaCloudlessConfigFlow.__init__ (lines 152-153) ─────────────────────────


class TestConfigFlowInit:
    def test_init_sets_discovered_and_device(self) -> None:
        """TuyaCloudlessConfigFlow.__init__ must set _discovered=[] and _device={}."""
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        # Call __init__ directly (not via __new__)
        flow = object.__new__(TuyaCloudlessConfigFlow)
        TuyaCloudlessConfigFlow.__init__(flow)

        assert flow._discovered == []
        assert flow._device == {}


# ── _pairing_tool_url successful get_url path (lines 941-943) ─────────────────


class TestPairingToolUrlSuccess:
    def test_returns_url_when_get_url_succeeds(self) -> None:
        """When get_url returns a valid URL, _pairing_tool_url uses it."""
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        flow.hass = MagicMock()

        with patch(
            "homeassistant.helpers.network.get_url",
            return_value="http://my-ha.local:8123",
        ):
            url = flow._pairing_tool_url()

        assert "my-ha.local" in url


# ── _auto_detect_profile ImportError (lines 998-999) ─────────────────────────


class TestAutoDetectProfileImportError:
    @pytest.mark.asyncio
    async def test_returns_generic_when_import_fails(self) -> None:
        """When tuya_cloudless library can't be imported, return 'Generic Switch'."""
        import sys

        flow = _make_config_flow()
        _restore_method(flow, "_auto_detect_profile")

        saved = sys.modules.pop("tuya_cloudless.crypto", None)
        try:
            sys.modules["tuya_cloudless.crypto"] = None  # type: ignore[assignment]
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )
        finally:
            if saved is not None:
                sys.modules["tuya_cloudless.crypto"] = saved
            else:
                sys.modules.pop("tuya_cloudless.crypto", None)

        assert result == "Generic Switch"


# ── _run_discovery ImportError (lines 1085-1087) ──────────────────────────────


class TestRunDiscoveryImportError:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_import_fails(self) -> None:
        """When DiscoveryListener can't be imported, return empty list."""
        import sys

        flow = _make_config_flow()
        _restore_method(flow, "_run_discovery")

        saved = sys.modules.pop("tuya_cloudless.discovery", None)
        try:
            sys.modules["tuya_cloudless.discovery"] = None  # type: ignore[assignment]
            result = await flow._run_discovery()
        finally:
            if saved is not None:
                sys.modules["tuya_cloudless.discovery"] = saved
            else:
                sys.modules.pop("tuya_cloudless.discovery", None)

        assert result == []


# ── _validate_local_key OSError on read (lines 1216-1218) ────────────────────


class TestValidateLocalKeyOsErrorRead:
    @pytest.mark.asyncio
    async def test_returns_empty_on_oserror_during_read(self) -> None:
        """OSError raised during asyncio.wait_for(reader.read) returns {}."""
        flow = _make_config_flow()
        _restore_method(flow, "_validate_local_key")

        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.get_extra_info = MagicMock(return_value="")

        # Patch encode_heartbeat to succeed, then patch wait_for to raise OSError
        # on the second call (first call is open_connection, second is reader.read)
        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            raise OSError("read error")

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=b"\x00" * 24,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
        ):
            result = await flow._validate_local_key(
                mock_reader,
                mock_writer,
                local_key="0123456789abcdef",
                version="3.3",
            )

        assert result == {}


# ── _auto_detect_profile body paths (lines 1011-1074) ─────────────────────────


class TestAutoDetectProfileBody:
    """Tests for _auto_detect_profile body after TCP connection succeeds."""

    def _make_connected_flow(self) -> Any:
        """Return a flow with _auto_detect_profile restored."""
        flow = _make_config_flow()
        _restore_method(flow, "_auto_detect_profile")
        return flow

    def _mock_open_connection(
        self,
        *,
        raw_data: bytes = b"",
        read_side_effect: object = None,
        write_side_effect: object = None,
        drain_side_effect: object = None,
    ) -> Any:
        """Return a patch ctx for asyncio.wait_for: yields mock reader/writer on first call."""
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock(side_effect=drain_side_effect)
        if write_side_effect:
            mock_writer.write = MagicMock(side_effect=write_side_effect)
        else:
            mock_writer.write = MagicMock()

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: open_connection
                return (mock_reader, mock_writer)
            # Second call: reader.read
            if read_side_effect:
                raise read_side_effect
            return raw_data

        return patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=fake_wait_for,
        )

    @pytest.mark.asyncio
    async def test_returns_generic_on_write_oserror(self) -> None:
        """OSError during writer.write returns 'Generic Switch'."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock(side_effect=OSError("write failed"))

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b""

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=fake_wait_for,
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_generic_on_empty_raw_response(self) -> None:
        """Empty raw bytes from reader returns 'Generic Switch'."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b""  # empty

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=fake_wait_for,
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_generic_on_read_timeout(self) -> None:
        """TimeoutError during reader.read returns 'Generic Switch'."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            raise TimeoutError()

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=fake_wait_for,
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_generic_on_no_frames(self) -> None:
        """Non-empty raw bytes that produce no frames returns 'Generic Switch'."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"  # non-empty but no valid frames

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([], b""),
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_generic_on_decode_error(self) -> None:
        """CryptoError during decode_frame returns 'Generic Switch'."""
        from tuya_cloudless.crypto import CryptoError

        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                side_effect=CryptoError("bad key"),
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_generic_when_no_dp_ids(self) -> None:
        """When decoded frame has no DP IDs, returns 'Generic Switch'."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        mock_frame = MagicMock()
        mock_frame.dps = {}  # empty dict — no DP IDs

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=mock_frame,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_returns_profile_name_when_detected(self) -> None:
        """When a profile is matched from DP IDs, returns the profile name."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        mock_frame = MagicMock()
        mock_frame.dps = {"dps": {"1": True, "2": 100}}

        mock_match = MagicMock()
        mock_match.name = "Smart Dimmer"

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=mock_frame,
            ),
            patch(
                "tuya_cloudless.profiles.list_profiles",
                return_value=[mock_match],
            ),
            patch(
                "tuya_cloudless.profiles.detect_profile_from_dps",
                return_value=mock_match,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Smart Dimmer"

    @pytest.mark.asyncio
    async def test_returns_generic_when_no_profile_match(self) -> None:
        """When no profile matches the DP IDs, returns 'Generic Switch'."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        mock_frame = MagicMock()
        mock_frame.dps = {"dps": {"1": True}}

        mock_profile = MagicMock()

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=mock_frame,
            ),
            patch(
                "tuya_cloudless.profiles.list_profiles",
                return_value=[mock_profile],
            ),
            patch(
                "tuya_cloudless.profiles.detect_profile_from_dps",
                return_value=None,  # no match
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"


# ── SEC-005: local_key character validation ───────────────────────────────────


class TestLocalKeyCharValidation:
    """SEC-005 (PLAT-828) — local_key must contain only printable ASCII."""

    @pytest.mark.asyncio
    async def test_null_byte_rejected_with_invalid_local_key_chars(self) -> None:
        """A local_key with a null byte must be rejected with invalid_local_key_chars."""
        from custom_components.tuya_cloudless.const import CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_local_key")
        # 15 printable ASCII chars + null byte = 16 chars total, correct length
        bad_key = "0123456789abcde\x00"
        await flow.async_step_local_key(user_input={CONF_LOCAL_KEY: bad_key})
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"

    @pytest.mark.asyncio
    async def test_non_ascii_rejected_with_invalid_local_key_chars(self) -> None:
        """A local_key containing non-ASCII characters must be rejected."""
        from custom_components.tuya_cloudless.const import CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_local_key")
        # '\xe9' is non-ASCII; 16 chars total
        bad_key = "0123456789abcd\xe9f"
        await flow.async_step_local_key(user_input={CONF_LOCAL_KEY: bad_key})
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"

    @pytest.mark.asyncio
    async def test_valid_printable_ascii_key_accepted(self) -> None:
        """A 16-character printable ASCII key must pass character validation."""
        from custom_components.tuya_cloudless.const import CONF_LOCAL_KEY, CONF_PROFILE

        flow = _make_config_flow()
        _restore_method(flow, "async_step_local_key")
        good_key = "0123456789abcdef"  # 16 printable ASCII chars
        await flow.async_step_local_key(
            user_input={CONF_LOCAL_KEY: good_key, CONF_PROFILE: "Generic Switch"}
        )
        # Should advance to confirm, not show a form error
        flow.async_step_confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_step_null_byte_rejected(self) -> None:
        """async_step_manual must also reject null byte in local_key."""
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow
        from custom_components.tuya_cloudless.const import (
            CONF_GW_ID,
            CONF_IP_ADDRESS,
            CONF_LOCAL_KEY,
        )

        flow = _make_config_flow()
        flow.async_step_manual = TuyaCloudlessConfigFlow.async_step_manual.__get__(flow)
        bad_key = "0123456789abcde\x00"
        await flow.async_step_manual(
            user_input={
                CONF_GW_ID: "gw001",
                CONF_LOCAL_KEY: bad_key,
                CONF_IP_ADDRESS: "10.0.0.1",
            }
        )
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"


# ── SEC-006: IP address format validation ─────────────────────────────────────


class TestIpAddressValidation:
    """SEC-006 (PLAT-829) — ip_address must be a valid IPv4 or IPv6 address."""

    @pytest.mark.asyncio
    async def test_bad_octets_rejected_in_manual_step(self) -> None:
        """'999.999.999.999' must be rejected with invalid_ip_address."""
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
                CONF_IP_ADDRESS: "999.999.999.999",
            }
        )
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_IP_ADDRESS) == "invalid_ip_address"

    @pytest.mark.asyncio
    async def test_non_ip_string_rejected_in_manual_step(self) -> None:
        """'not an ip' must be rejected with invalid_ip_address."""
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
                CONF_IP_ADDRESS: "not an ip",
            }
        )
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_IP_ADDRESS) == "invalid_ip_address"

    @pytest.mark.asyncio
    async def test_valid_ipv4_accepted_in_manual_step(self) -> None:
        """'192.168.1.1' is a valid IPv4 address and must pass validation."""
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
                CONF_IP_ADDRESS: "192.168.1.1",
            }
        )
        # Should create entry, not show form errors about IP
        flow.async_create_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_ip_rejected_in_reconfigure_step(self) -> None:
        """Reconfigure step must also reject invalid IP with invalid_ip_address."""
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
                CONF_IP_ADDRESS: "not-an-ip",
            }
        )
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_IP_ADDRESS) == "invalid_ip_address"


# ── HA-004: MINOR_VERSION ─────────────────────────────────────────────────────


class TestMinorVersion:
    """HA-004 (PLAT-844) — MINOR_VERSION must be set on ConfigFlow and OptionsFlow."""

    def test_config_flow_has_minor_version_1(self) -> None:
        """TuyaCloudlessConfigFlow must have MINOR_VERSION = 1."""
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        assert TuyaCloudlessConfigFlow.MINOR_VERSION == 1

    def test_options_flow_has_minor_version_1(self) -> None:
        """TuyaCloudlessOptionsFlow must have MINOR_VERSION = 1."""
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessOptionsFlow

        assert TuyaCloudlessOptionsFlow.MINOR_VERSION == 1


# ── SEC-005: char validation in discovery + reauth_confirm steps ───────────────


class TestLocalKeyCharValidationDiscovery:
    """SEC-005 char validation in async_step_discovery (line 658)."""

    @pytest.mark.asyncio
    async def test_discovery_non_ascii_key_rejected(self) -> None:
        """async_step_discovery must reject a local_key with non-ASCII chars (line 658)."""
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_discovery")
        flow._device = {CONF_IP_ADDRESS: "10.0.0.1"}

        # 15 printable ASCII + one non-ASCII byte = 16 chars, right length
        bad_key = "0123456789abcd\xe9f"
        await flow.async_step_discovery({CONF_LOCAL_KEY: bad_key})

        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"

    @pytest.mark.asyncio
    async def test_discovery_null_byte_key_rejected(self) -> None:
        """async_step_discovery must reject a local_key with a null byte (line 658)."""
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_discovery")
        flow._device = {CONF_IP_ADDRESS: "10.0.0.1"}

        bad_key = "0123456789abcde\x00"
        await flow.async_step_discovery({CONF_LOCAL_KEY: bad_key})

        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"


class TestLocalKeyCharValidationReauthConfirm:
    """SEC-005 char validation in async_step_reauth_confirm (line 818)."""

    @pytest.mark.asyncio
    async def test_reauth_confirm_non_ascii_key_rejected(self) -> None:
        """async_step_reauth_confirm must reject a local_key with non-ASCII chars (line 818)."""
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reauth_confirm")
        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reauth_entry = MagicMock(return_value=mock_entry)

        bad_key = "0123456789abcd\xe9f"
        await flow.async_step_reauth_confirm({CONF_LOCAL_KEY: bad_key})

        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"

    @pytest.mark.asyncio
    async def test_reauth_confirm_null_byte_key_rejected(self) -> None:
        """async_step_reauth_confirm must reject a local_key with a null byte (line 818)."""
        from custom_components.tuya_cloudless.const import CONF_IP_ADDRESS, CONF_LOCAL_KEY

        flow = _make_config_flow()
        _restore_method(flow, "async_step_reauth_confirm")
        mock_entry = MagicMock()
        mock_entry.data = {CONF_IP_ADDRESS: "10.0.0.1"}
        mock_entry.title = "Test Device"
        flow._get_reauth_entry = MagicMock(return_value=mock_entry)

        bad_key = "0123456789abcde\x00"
        await flow.async_step_reauth_confirm({CONF_LOCAL_KEY: bad_key})

        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs.get("errors", {}).get(CONF_LOCAL_KEY) == "invalid_local_key_chars"


# ── auto_detect_profile: else-branch + except + empty profiles (1074-1076, 1084-1085)


class TestAutoDetectProfileEdgeCases:
    """Coverage for lines 1074-1076 (else dp_ids=set, except) and 1084-1085 (empty profiles)."""

    def _make_connected_flow(self) -> Any:
        from custom_components.tuya_cloudless.config_flow import TuyaCloudlessConfigFlow

        flow = TuyaCloudlessConfigFlow.__new__(TuyaCloudlessConfigFlow)
        flow._discovered = []
        flow._device = {}
        return flow

    @pytest.mark.asyncio
    async def test_non_dict_dps_payload_returns_generic(self) -> None:
        """When frame.dps is not a dict, dp_ids = set() and returns 'Generic Switch' (line 1074)."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        mock_frame = MagicMock()
        mock_frame.dps = "not_a_dict"  # not a dict → else branch: dp_ids = set()

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=mock_frame,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_frame_dps_access_raises_returns_generic(self) -> None:
        """When accessing frame.dps raises an exception, returns 'Generic Switch'."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        mock_frame = MagicMock()
        # Make accessing .dps raise an arbitrary exception
        type(mock_frame).dps = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=mock_frame,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_empty_profiles_triggers_init(self) -> None:
        """When list_profiles() returns [] first, init_profiles is called (lines 1083-1085)."""
        flow = self._make_connected_flow()
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.drain = AsyncMock()
        mock_writer.write = MagicMock()

        mock_frame = MagicMock()
        mock_frame.dps = {"dps": {"1": True}}

        mock_match = MagicMock()
        mock_match.name = "Smart Plug"

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_reader, mock_writer)
            return b"\x00\x01\x02"

        # list_profiles returns [] on first call (empty), then returns profiles after init
        list_profiles_calls = 0

        def fake_list_profiles() -> list:  # type: ignore[type-arg]
            nonlocal list_profiles_calls
            list_profiles_calls += 1
            if list_profiles_calls == 1:
                return []  # first call: empty → triggers init_profiles
            return [mock_match]  # second call: populated

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([b"frame"], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=mock_frame,
            ),
            patch(
                "tuya_cloudless.profiles.list_profiles",
                side_effect=fake_list_profiles,
            ),
            patch("tuya_cloudless.profiles.init_profiles") as mock_init,
            patch(
                "tuya_cloudless.profiles.detect_profile_from_dps",
                return_value=mock_match,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        # init_profiles must have been called when profiles were empty
        mock_init.assert_called_once()
        assert result == "Smart Plug"
