"""Additional config flow tests — coverage pass 2 (PLAT-780).

Targets all lines that remained uncovered after the first two test files:

  Lines 93-94    _get_profile_options      ImportError path
  Lines 129-130  _suggest_profile          ImportError path
  Lines 210-247  async_step_ble_pair       both branches
  Lines 266-311  async_step_ble_confirm    both branches (form + entry creation)
  Lines 355-364  async_step_ha_url         empty URL error + valid URL branch
  Lines 522-527  async_step_confirm        __auto_detect__ profile resolution (PLAT-778)
  Lines 943-947  async_remove              pairing-server cleanup
  Lines 965-967  _pairing_tool_url         get_url success path
  Lines 1013-98  _auto_detect_profile      every exit path
  Lines 1109-11  _run_discovery            ImportError path
  Lines 1221-23  _validate_local_key       encode_heartbeat raises CryptoError / UnsupportedVersionError
  Lines 1240-42  _validate_local_key       OSError on reader.read
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the standalone library is importable when running tests directly.
_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from custom_components.tuya_cloudless.config_flow import (
    TuyaCloudlessConfigFlow,
    _get_profile_options,
    _suggest_profile,
)
from homeassistant.helpers.network import NoURLAvailableError
from custom_components.tuya_cloudless.const import (
    CONF_DEVICE_NAME,
    CONF_GW_ID,
    CONF_IP_ADDRESS,
    CONF_LOCAL_KEY,
    CONF_PROFILE,
    CONF_PROTOCOL_VERSION,
    DEFAULT_PROTOCOL_VERSION,
)


# ── Shared factory ─────────────────────────────────────────────────────────────


def _make_flow() -> TuyaCloudlessConfigFlow:
    """Return a TuyaCloudlessConfigFlow with all HA base-class methods mocked.

    Uses TuyaCloudlessConfigFlow() (real __init__) so instance attributes
    (``_discovered``, ``_device``, ``_ha_base_url``) are properly set.
    """
    flow = TuyaCloudlessConfigFlow()
    flow.hass = MagicMock()
    flow.flow_id = "test-flow-id-001"
    flow.context = {}
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_abort = MagicMock(return_value={"type": "abort"})
    flow.async_external_step = MagicMock(return_value={"type": "external"})
    flow.async_external_step_done = MagicMock(return_value={"type": "external_done"})
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    return flow


# ── _get_profile_options — ImportError branch (lines 93-94) ───────────────────


class TestGetProfileOptionsImportError:
    """_get_profile_options must fall back to [Auto-detect, Generic Switch] on ImportError."""

    def test_import_error_returns_auto_detect_and_generic_switch(self) -> None:
        """When tuya_cloudless.profiles cannot be imported the fallback list is returned."""
        # Block the lazy import inside _get_profile_options by replacing the
        # already-loaded module with None (causes ImportError on 'from' import).
        with patch.dict("sys.modules", {"tuya_cloudless.profiles": None}):
            options = _get_profile_options()

        values = [o["value"] for o in options]
        # Auto-detect sentinel is always first (PLAT-778)
        assert options[0]["value"] == "__auto_detect__"
        # Fallback "Generic Switch" entry must be present
        assert "Generic Switch" in values

    def test_import_error_returns_exactly_two_options(self) -> None:
        """Exactly two options (Auto-detect + Generic Switch) on import failure."""
        with patch.dict("sys.modules", {"tuya_cloudless.profiles": None}):
            options = _get_profile_options()
        assert len(options) == 2


# ── _suggest_profile — ImportError branch (lines 129-130) ─────────────────────


class TestSuggestProfileImportError:
    """_suggest_profile must return the auto-detect sentinel when the profiles
    module is unavailable, even if a product_key is provided."""

    def test_import_error_returns_auto_detect_sentinel(self) -> None:
        """ImportError during find_profile_by_product_key → __auto_detect__."""
        with patch.dict("sys.modules", {"tuya_cloudless.profiles": None}):
            result = _suggest_profile("any_product_key")
        assert result == "__auto_detect__"

    def test_import_error_with_none_key_returns_auto_detect(self) -> None:
        """None product_key short-circuits before the import — still __auto_detect__."""
        with patch.dict("sys.modules", {"tuya_cloudless.profiles": None}):
            result = _suggest_profile(None)
        assert result == "__auto_detect__"

    def test_wildcard_model_returns_auto_detect(self) -> None:
        """A profile whose model == '*' is considered a generic fallback.

        _suggest_profile should NOT suggest it — return __auto_detect__ instead.
        """
        mock_profile = MagicMock()
        mock_profile.name = "Generic Switch"
        mock_profile.model = "*"  # wildcard — must NOT be suggested
        with patch(
            "tuya_cloudless.profiles.find_profile_by_product_key",
            return_value=mock_profile,
        ):
            result = _suggest_profile("some_key")
        assert result == "__auto_detect__"


# ── async_step_ble_pair (lines 210-247) ────────────────────────────────────────


class TestAsyncStepBlePair:
    """Tests for async_step_ble_pair — second-call (device data) and first-call paths."""

    @pytest.mark.asyncio
    async def test_second_call_stores_device_and_returns_external_done(self) -> None:
        """When user_input contains device data the flow stores it and returns
        async_external_step_done pointing at 'ble_confirm'."""
        flow = _make_flow()

        result = await flow.async_step_ble_pair(
            user_input={
                CONF_GW_ID: "abcd1234",
                CONF_LOCAL_KEY: "0123456789abcdef",
                "ip_address": "192.168.1.55",
                "product_key": "pk_test_001",
            }
        )

        flow.async_external_step_done.assert_called_once_with(next_step_id="ble_confirm")
        assert flow._device[CONF_GW_ID] == "abcd1234"
        assert flow._device[CONF_LOCAL_KEY] == "0123456789abcdef"
        assert flow._device[CONF_IP_ADDRESS] == "192.168.1.55"
        assert flow._device["product_key"] == "pk_test_001"

    @pytest.mark.asyncio
    async def test_second_call_strips_whitespace_from_fields(self) -> None:
        """Fields with leading/trailing whitespace must be stripped."""
        flow = _make_flow()

        await flow.async_step_ble_pair(
            user_input={
                CONF_GW_ID: "  gw_ws  ",
                CONF_LOCAL_KEY: "  abcd1234abcd1234  ",  # 16 alphanumeric chars after strip
                "ip_address": "  10.0.0.1  ",
                "product_key": "  pk  ",
            }
        )

        assert flow._device[CONF_GW_ID] == "gw_ws"
        assert flow._device[CONF_IP_ADDRESS] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_first_call_starts_server_and_returns_external_step(self) -> None:
        """First call (user_input=None) must start the pairing server, register the
        flow, then return an async_external_step pointing to the pairing URL."""
        flow = _make_flow()

        mock_server = MagicMock()
        mock_server.register_flow = MagicMock()
        mock_server.ha_ui_url = MagicMock(return_value="http://192.168.1.1:8099")

        # ensure_pairing_server is imported inside the method body as:
        #   from .pairing_server import ensure_pairing_server
        # so we patch it on the pairing_server module directly.
        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
                new=AsyncMock(return_value=mock_server),
            ),
            patch(
                "homeassistant.helpers.network.get_url",
                return_value="https://homeassistant.local:8123",
            ),
        ):
            result = await flow.async_step_ble_pair(user_input=None)

        mock_server.register_flow.assert_called_once_with("test-flow-id-001")
        flow.async_external_step.assert_called_once()
        # The URL must include the flow_id so the pairing server can resume us.
        call_kwargs = flow.async_external_step.call_args[1]
        assert "test-flow-id-001" in call_kwargs["url"]
        assert call_kwargs["step_id"] == "ble_pair"

    @pytest.mark.asyncio
    async def test_first_call_server_oserror_aborts(self) -> None:
        """OSError from ensure_pairing_server → async_abort(reason='pairing_server_unavailable')."""
        flow = _make_flow()

        with patch(
            "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
            new=AsyncMock(side_effect=OSError("port in use")),
        ):
            await flow.async_step_ble_pair(user_input=None)

        flow.async_abort.assert_called_once_with(reason="pairing_server_unavailable")

    @pytest.mark.asyncio
    async def test_first_call_falls_back_to_ha_url_when_get_url_fails(self) -> None:
        """When no HTTPS URL is available the flow falls back to async_step_ble_fallback."""
        flow = _make_flow()

        mock_server = MagicMock()
        mock_server.register_flow = MagicMock()
        mock_server.ha_ui_url = MagicMock(return_value="http://192.168.1.1:8099")

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
                new=AsyncMock(return_value=mock_server),
            ),
            patch(
                "homeassistant.helpers.network.get_url",
                side_effect=NoURLAvailableError("no HTTPS URL configured"),
            ),
        ):
            flow.async_step_ble_fallback = AsyncMock(
                return_value={"type": "form", "step_id": "ble_fallback"}
            )
            result = await flow.async_step_ble_pair(user_input=None)

        flow.async_step_ble_fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_call_uses_pre_set_ha_base_url(self) -> None:
        """When _ha_base_url is already set, get_url is skipped and the stored URL is used."""
        flow = _make_flow()
        flow._manual_ha_url = "https://192.168.5.22:8123"

        mock_server = MagicMock()
        mock_server.register_flow = MagicMock()

        with patch(
            "custom_components.tuya_cloudless.pairing_server.ensure_pairing_server",
            new=AsyncMock(return_value=mock_server),
        ):
            result = await flow.async_step_ble_pair(user_input=None)

        flow.async_external_step.assert_called_once()
        call_kwargs = flow.async_external_step.call_args[1]
        assert "192.168.5.22" in call_kwargs["url"]


# ── async_step_ble_confirm (lines 266-311) ────────────────────────────────────


class TestAsyncStepBleConfirm:
    """Tests for async_step_ble_confirm — form rendering and entry creation."""

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        """First render (user_input=None) must show the confirmation form."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_ble_001",
            CONF_IP_ADDRESS: "10.0.0.2",
            CONF_LOCAL_KEY: "0123456789abcdef",
            "product_key": "pk_001",
        }

        with patch(
            "custom_components.tuya_cloudless.config_flow._get_profile_options",
            return_value=[
                {"value": "__auto_detect__", "label": "Auto-detect (recommended)"},
                {"value": "Generic Switch", "label": "Generic Switch"},
            ],
        ):
            await flow.async_step_ble_confirm(user_input=None)

        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "ble_confirm"

    @pytest.mark.asyncio
    async def test_no_input_form_includes_ip_and_gw_id_placeholders(self) -> None:
        """The form description_placeholders must include ip_address and gw_id."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_ble_002",
            CONF_IP_ADDRESS: "10.0.0.9",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }

        with patch(
            "custom_components.tuya_cloudless.config_flow._get_profile_options",
            return_value=[{"value": "__auto_detect__", "label": "Auto-detect"}],
        ):
            await flow.async_step_ble_confirm(user_input=None)

        call_kwargs = flow.async_show_form.call_args[1]
        placeholders = call_kwargs["description_placeholders"]
        assert placeholders["ip_address"] == "10.0.0.9"
        assert placeholders["gw_id"] == "gw_ble_002"

    @pytest.mark.asyncio
    async def test_valid_input_creates_config_entry(self) -> None:
        """Submitting the form must create a config entry with the correct data."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_ble_003",
            CONF_IP_ADDRESS: "10.0.0.3",
            CONF_LOCAL_KEY: "fedcba9876543210",
        }

        mock_server = MagicMock()
        mock_server.unregister_flow = MagicMock()

        # get_pairing_server is imported inside the method body as:
        #   from .pairing_server import get_pairing_server
        # Patch it on the pairing_server module so the local import picks it up.
        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=mock_server,
        ):
            result = await flow.async_step_ble_confirm(
                user_input={
                    CONF_DEVICE_NAME: "BLE Lamp",
                    CONF_PROFILE: "Generic Light",
                }
            )

        flow.async_set_unique_id.assert_awaited_once_with("gw_ble_003")
        flow._abort_if_unique_id_configured.assert_called_once()
        flow.async_create_entry.assert_called_once()
        call_kwargs = flow.async_create_entry.call_args[1]
        assert call_kwargs["title"] == "BLE Lamp"
        data = call_kwargs["data"]
        assert data[CONF_GW_ID] == "gw_ble_003"
        assert data[CONF_LOCAL_KEY] == "fedcba9876543210"
        assert data[CONF_PROFILE] == "Generic Light"

    @pytest.mark.asyncio
    async def test_valid_input_unregisters_flow_from_pairing_server(self) -> None:
        """On submission the flow must be unregistered from the pairing server."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_ble_004",
            CONF_IP_ADDRESS: "10.0.0.4",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }

        mock_server = MagicMock()
        mock_server.unregister_flow = MagicMock()

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=mock_server,
        ):
            await flow.async_step_ble_confirm(
                user_input={
                    CONF_DEVICE_NAME: "Test Device",
                    CONF_PROFILE: "Generic Switch",
                }
            )

        mock_server.unregister_flow.assert_called_once_with("test-flow-id-001")

    @pytest.mark.asyncio
    async def test_valid_input_no_pairing_server_does_not_crash(self) -> None:
        """get_pairing_server returning None must not cause an exception."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_ble_005",
            CONF_IP_ADDRESS: "10.0.0.5",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=None,
        ):
            # Must not raise even when no server is registered.
            await flow.async_step_ble_confirm(
                user_input={
                    CONF_DEVICE_NAME: "No Server Device",
                    CONF_PROFILE: "Generic Switch",
                }
            )

        flow.async_create_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_device_name_defaults_to_ip(self) -> None:
        """Empty device_name in user_input must fall back to the IP address."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_ble_006",
            CONF_IP_ADDRESS: "10.0.0.6",
            CONF_LOCAL_KEY: "0123456789abcdef",
        }

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=None,
        ):
            await flow.async_step_ble_confirm(
                user_input={CONF_DEVICE_NAME: "", CONF_PROFILE: "Generic Switch"}
            )

        call_kwargs = flow.async_create_entry.call_args[1]
        assert call_kwargs["title"] == "10.0.0.6"


# ── async_step_ha_url (lines 355-364) ─────────────────────────────────────────


class TestAsyncStepHaUrl:
    """Tests for the fallback HA-address collection step (async_step_ble_ha_url)."""

    @pytest.mark.asyncio
    async def test_no_input_shows_form(self) -> None:
        """First render shows the ble_ha_url form without errors."""
        flow = _make_flow()
        await flow.async_step_ble_ha_url(user_input=None)
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["step_id"] == "ble_ha_url"

    @pytest.mark.asyncio
    async def test_empty_url_shows_error(self) -> None:
        """Submitting an empty ha_url must re-render the form with an error."""
        flow = _make_flow()
        await flow.async_step_ble_ha_url(user_input={"ha_url": "   "})
        flow.async_show_form.assert_called_once()
        call_kwargs = flow.async_show_form.call_args[1]
        assert call_kwargs["errors"].get("ha_url") == "invalid_ha_url_https"

    @pytest.mark.asyncio
    async def test_valid_url_stores_and_continues_to_ble_pair(self) -> None:
        """A valid HTTPS URL must be stored in _manual_ha_url and flow advances
        to async_step_ble_pair."""
        flow = _make_flow()
        flow.async_step_ble_pair = AsyncMock(return_value={"type": "external"})

        await flow.async_step_ble_ha_url(user_input={"ha_url": "https://192.168.1.100:8123"})

        # Trailing slash must be stripped as per the code (rstrip("/"))
        assert flow._manual_ha_url == "https://192.168.1.100:8123"
        flow.async_step_ble_pair.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_url_with_trailing_slash_is_stripped(self) -> None:
        """Trailing slashes in ha_url must be removed before storing."""
        flow = _make_flow()
        flow.async_step_ble_pair = AsyncMock(return_value={"type": "external"})

        await flow.async_step_ble_ha_url(user_input={"ha_url": "https://ha.local:8123/"})

        assert not flow._manual_ha_url.endswith("/")


# ── async_step_confirm — __auto_detect__ profile path (lines 522-527) ─────────


class TestAsyncStepConfirmAutoDetectProfile:
    """When profile == __auto_detect__, the confirm step must call _auto_detect_profile
    and store the detected name before creating the config entry (PLAT-778)."""

    @pytest.mark.asyncio
    async def test_auto_detect_profile_is_called_and_result_stored(self) -> None:
        """_auto_detect_profile must be awaited and its result stored in _device."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_auto",
            CONF_IP_ADDRESS: "10.0.0.20",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_PROFILE: "__auto_detect__",
            CONF_DEVICE_NAME: "Auto Light",
        }
        flow._check_connection = AsyncMock(return_value={})
        flow._auto_detect_profile = AsyncMock(return_value="Generic Light")

        await flow.async_step_confirm(user_input={})

        flow._auto_detect_profile.assert_awaited_once_with(
            "10.0.0.20",
            local_key="0123456789abcdef",
            version="3.3",
        )
        assert flow._device[CONF_PROFILE] == "Generic Light"

    @pytest.mark.asyncio
    async def test_auto_detect_profile_result_used_in_entry_data(self) -> None:
        """The profile detected by _auto_detect_profile must appear in the saved entry data."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_auto2",
            CONF_IP_ADDRESS: "10.0.0.21",
            CONF_LOCAL_KEY: "abcdef0123456789",
            CONF_PROTOCOL_VERSION: "3.4",
            CONF_PROFILE: "__auto_detect__",
            CONF_DEVICE_NAME: "Smart Outlet",
        }
        flow._check_connection = AsyncMock(return_value={})
        flow._auto_detect_profile = AsyncMock(return_value="Smart Plug")

        await flow.async_step_confirm(user_input={})

        flow.async_create_entry.assert_called_once()
        entry_data = flow.async_create_entry.call_args[1]["data"]
        assert entry_data[CONF_PROFILE] == "Smart Plug"

    @pytest.mark.asyncio
    async def test_auto_detect_profile_fallback_to_generic_switch(self) -> None:
        """When auto-detection cannot identify a profile it must fall back to
        'Generic Switch' and that fallback must appear in the entry data."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_fallback",
            CONF_IP_ADDRESS: "10.0.0.22",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_PROFILE: "__auto_detect__",
            CONF_DEVICE_NAME: "Unknown Device",
        }
        flow._check_connection = AsyncMock(return_value={})
        flow._auto_detect_profile = AsyncMock(return_value="Generic Switch")

        await flow.async_step_confirm(user_input={})

        entry_data = flow.async_create_entry.call_args[1]["data"]
        assert entry_data[CONF_PROFILE] == "Generic Switch"

    @pytest.mark.asyncio
    async def test_non_auto_profile_skips_auto_detect(self) -> None:
        """When an explicit profile is selected _auto_detect_profile must NOT be called."""
        flow = _make_flow()
        flow._device = {
            CONF_GW_ID: "gw_explicit",
            CONF_IP_ADDRESS: "10.0.0.23",
            CONF_LOCAL_KEY: "0123456789abcdef",
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_PROFILE: "Generic Light",
            CONF_DEVICE_NAME: "My Light",
        }
        flow._check_connection = AsyncMock(return_value={})
        flow._auto_detect_profile = AsyncMock(return_value="Should Not Be Called")

        await flow.async_step_confirm(user_input={})

        flow._auto_detect_profile.assert_not_called()
        entry_data = flow.async_create_entry.call_args[1]["data"]
        assert entry_data[CONF_PROFILE] == "Generic Light"


# ── async_remove (lines 943-947) ──────────────────────────────────────────────


class TestAsyncRemove:
    """async_remove must unregister the flow from the pairing server on cancellation."""

    @pytest.mark.asyncio
    async def test_unregisters_from_pairing_server_when_server_exists(self) -> None:
        """The pairing server's unregister_flow must be called with the flow_id."""
        flow = _make_flow()

        mock_server = MagicMock()
        mock_server.unregister_flow = MagicMock()

        # get_pairing_server is imported inside async_remove as:
        #   from .pairing_server import get_pairing_server
        # Patch on the pairing_server module so the local import picks it up.
        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=mock_server,
        ):
            await flow.async_remove()

        mock_server.unregister_flow.assert_called_once_with("test-flow-id-001")

    @pytest.mark.asyncio
    async def test_does_not_crash_when_no_server(self) -> None:
        """async_remove must be a no-op when get_pairing_server returns None."""
        flow = _make_flow()

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=None,
        ):
            # Must not raise
            await flow.async_remove()

    @pytest.mark.asyncio
    async def test_server_looked_up_with_hass(self) -> None:
        """get_pairing_server must be called with the flow's hass instance."""
        flow = _make_flow()
        hass_sentinel = flow.hass  # capture before the call

        with patch(
            "custom_components.tuya_cloudless.pairing_server.get_pairing_server",
            return_value=None,
        ) as mock_get:
            await flow.async_remove()

        mock_get.assert_called_once_with(hass_sentinel)


# ── _pairing_tool_url — get_url success path (lines 965-967) ─────────────────


class TestPairingToolUrlGetUrlSuccess:
    """_pairing_tool_url must use homeassistant.helpers.network.get_url when available."""

    def test_uses_get_url_hostname_when_successful(self) -> None:
        """When get_url returns a URL the hostname must be extracted from it."""
        flow = _make_flow()

        with patch(
            "homeassistant.helpers.network.get_url",
            return_value="http://192.168.5.10:8123",
        ):
            url = flow._pairing_tool_url()

        # Must be http://<hostname>:<PAIRING_SERVER_PORT>
        assert url.startswith("http://192.168.5.10:")
        assert ":8099" in url

    def test_get_url_result_takes_priority_over_internal_url(self) -> None:
        """get_url success must be used even if hass.config.internal_url is set."""
        flow = _make_flow()
        flow.hass.config.internal_url = "http://different-host:8123"

        with patch(
            "homeassistant.helpers.network.get_url",
            return_value="http://correct-host:8123",
        ):
            url = flow._pairing_tool_url()

        assert "correct-host" in url

    def test_falls_back_to_internal_url_when_get_url_raises(self) -> None:
        """When get_url raises, _pairing_tool_url must fall back to internal_url."""
        flow = _make_flow()
        flow.hass.config.internal_url = "http://fallback-host:8123"

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("no network"),
        ):
            url = flow._pairing_tool_url()

        assert "fallback-host" in url
        assert ":8099" in url


# ── _auto_detect_profile (lines 1013-1098) ────────────────────────────────────


class TestAutoDetectProfile:
    """Tests for every exit path of _auto_detect_profile."""

    @pytest.mark.asyncio
    async def test_import_error_returns_generic_switch(self) -> None:
        """If the tuya_cloudless library cannot be imported the method returns 'Generic Switch'."""
        flow = _make_flow()

        with patch.dict("sys.modules", {"tuya_cloudless.crypto": None}):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_connection_timeout_returns_generic_switch(self) -> None:
        """A TimeoutError opening the TCP connection must return 'Generic Switch'."""
        flow = _make_flow()

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=TimeoutError(),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.99", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_connection_oserror_returns_generic_switch(self) -> None:
        """An OSError opening the TCP connection must return 'Generic Switch'."""
        flow = _make_flow()

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=OSError("connection refused"),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.99", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_write_oserror_returns_generic_switch(self) -> None:
        """OSError while writing the DP_QUERY frame must return 'Generic Switch'."""
        from tuya_cloudless.crypto import CryptoError
        from tuya_cloudless.exceptions import MalformedPacketError

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock(side_effect=OSError("broken pipe"))
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                return_value=(AsyncMock(), writer),
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_read_timeout_returns_generic_switch(self) -> None:
        """TimeoutError while reading the DP_QUERY response returns 'Generic Switch'."""
        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: open_connection succeeds
                return (AsyncMock(), writer)
            # Second call: read times out
            raise TimeoutError()

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_empty_response_returns_generic_switch(self) -> None:
        """Empty response bytes from the device returns 'Generic Switch'."""
        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return b""  # empty response

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_no_frames_returns_generic_switch(self) -> None:
        """split_frames returning no complete frames returns 'Generic Switch'."""
        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return b"\xde\xad\xbe\xef"

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([], b"\xde\xad\xbe\xef"),
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_crypto_error_on_decode_returns_generic_switch(self) -> None:
        """CryptoError / MalformedPacketError during decode_frame returns 'Generic Switch'."""
        from tuya_cloudless.crypto import CryptoError

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
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
    async def test_empty_dp_ids_returns_generic_switch(self) -> None:
        """A decoded frame with no DP IDs returns 'Generic Switch' (nothing to match)."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24
        # Frame with no DPS payload
        good_frame = MagicMock(spec=TuyaFrame)
        good_frame.dps = {"dps": {}}

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
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
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_no_profile_match_returns_generic_switch(self) -> None:
        """detect_profile_from_dps returning None falls back to 'Generic Switch'."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24
        good_frame = MagicMock(spec=TuyaFrame)
        good_frame.dps = {"dps": {"1": True, "7": 0}}

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=good_frame,
            ),
            patch(
                "tuya_cloudless.profiles.detect_profile_from_dps",
                return_value=None,
            ),
            patch(
                "tuya_cloudless.profiles.list_profiles",
                return_value=[MagicMock(name="SomeProfile")],
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_profile_matched_returns_profile_name(self) -> None:
        """A successful detect_profile_from_dps match must return the profile name."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24
        good_frame = MagicMock(spec=TuyaFrame)
        good_frame.dps = {"dps": {"1": True, "2": 100, "3": "white"}}

        matched_profile = MagicMock()
        matched_profile.name = "Smart Dimmer"

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=good_frame,
            ),
            patch(
                "tuya_cloudless.profiles.detect_profile_from_dps",
                return_value=matched_profile,
            ),
            patch(
                "tuya_cloudless.profiles.list_profiles",
                return_value=[matched_profile],
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Smart Dimmer"

    @pytest.mark.asyncio
    async def test_loads_profiles_when_list_is_empty(self) -> None:
        """When list_profiles returns [] the method must call init_profiles before matching."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24
        good_frame = MagicMock(spec=TuyaFrame)
        good_frame.dps = {"dps": {"1": True}}

        matched_profile = MagicMock()
        matched_profile.name = "Smart Plug"

        list_call_count = 0

        def fake_list_profiles() -> list:
            nonlocal list_call_count
            list_call_count += 1
            if list_call_count == 1:
                return []  # empty on first call → triggers init_profiles
            return [matched_profile]

        init_called: list[bool] = []

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=good_frame,
            ),
            patch(
                "tuya_cloudless.profiles.list_profiles",
                side_effect=fake_list_profiles,
            ),
            patch(
                "tuya_cloudless.profiles.init_profiles",
                side_effect=lambda _path: init_called.append(True),
            ),
            patch(
                "tuya_cloudless.profiles.detect_profile_from_dps",
                return_value=matched_profile,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert init_called, "init_profiles must have been called when list was empty"
        assert result == "Smart Plug"

    @pytest.mark.asyncio
    async def test_dps_payload_is_flat_dict(self) -> None:
        """When frame.dps is a plain dict (no nested 'dps' key) the keys must be used directly."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24
        good_frame = MagicMock(spec=TuyaFrame)
        # Flat dict — no "dps" nesting
        good_frame.dps = {"1": True, "2": False}

        matched_profile = MagicMock()
        matched_profile.name = "Generic Switch"

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=good_frame,
            ),
            patch(
                "tuya_cloudless.profiles.list_profiles",
                return_value=[matched_profile],
            ),
            patch(
                "tuya_cloudless.profiles.detect_profile_from_dps",
                return_value=matched_profile,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_non_dict_dps_returns_generic_switch(self) -> None:
        """When frame.dps is not a dict at all, the method must return 'Generic Switch'."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24
        good_frame = MagicMock(spec=TuyaFrame)
        good_frame.dps = None  # not a dict

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
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
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"

    @pytest.mark.asyncio
    async def test_dps_attribute_raises_exception_returns_generic_switch(self) -> None:
        """An unexpected exception while accessing frame.dps must be swallowed and
        return 'Generic Switch' (the bare 'except Exception' guard on line 1072-1073)."""
        from tuya_cloudless.protocol import TuyaFrame

        flow = _make_flow()

        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        dummy_frame = b"\x00\x00U\xaa" + b"\x00" * 24

        # A frame whose .dps property raises when accessed triggers the except block.
        bad_frame = MagicMock(spec=TuyaFrame)
        type(bad_frame).dps = property(lambda self: (_ for _ in ()).throw(RuntimeError("bad dps")))

        call_count = 0

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), writer)
            return dummy_frame

        with (
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "tuya_cloudless.protocol.encode_status_query",
                return_value=b"\x00" * 24,
            ),
            patch(
                "tuya_cloudless.protocol.split_frames",
                return_value=([dummy_frame], b""),
            ),
            patch(
                "tuya_cloudless.protocol.decode_frame",
                return_value=bad_frame,
            ),
        ):
            result = await flow._auto_detect_profile(
                "10.0.0.1", local_key="0123456789abcdef", version="3.3"
            )

        assert result == "Generic Switch"


# ── _run_discovery — ImportError branch (lines 1109-1111) ─────────────────────


class TestRunDiscoveryImportError:
    """_run_discovery must return an empty list when the discovery module is unavailable."""

    @pytest.mark.asyncio
    async def test_import_error_returns_empty_list(self) -> None:
        """If tuya_cloudless.discovery cannot be imported the method returns []."""
        flow = _make_flow()

        with patch.dict("sys.modules", {"tuya_cloudless.discovery": None}):
            result = await flow._run_discovery()

        assert result == []

    @pytest.mark.asyncio
    async def test_import_error_does_not_raise(self) -> None:
        """ImportError in _run_discovery must be swallowed — no exception propagates."""
        flow = _make_flow()

        with patch.dict("sys.modules", {"tuya_cloudless.discovery": None}):
            # Should return without raising
            result = await flow._run_discovery()
        assert isinstance(result, list)


# ── _validate_local_key — encode_heartbeat errors (lines 1221-1223) ──────────


class TestValidateLocalKeyEncodeHeartbeatErrors:
    """encode_heartbeat raising UnsupportedVersionError or CryptoError must be
    treated as inconclusive — the method returns {} without setting an error."""

    @pytest.mark.asyncio
    async def test_unsupported_version_error_returns_empty(self) -> None:
        """UnsupportedVersionError from encode_heartbeat → skip validation."""
        from tuya_cloudless.exceptions import UnsupportedVersionError

        flow = _make_flow()
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        with patch(
            "tuya_cloudless.protocol.encode_heartbeat",
            side_effect=UnsupportedVersionError("3.99"),
        ):
            result = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.99"
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_crypto_error_on_encode_returns_empty(self) -> None:
        """CryptoError from encode_heartbeat → skip validation, no error raised."""
        from tuya_cloudless.crypto import CryptoError

        flow = _make_flow()
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        with patch(
            "tuya_cloudless.protocol.encode_heartbeat",
            side_effect=CryptoError("encode failed"),
        ):
            result = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_method_does_not_raise_on_encode_failure(self) -> None:
        """Neither UnsupportedVersionError nor CryptoError must propagate to the caller."""
        from tuya_cloudless.exceptions import UnsupportedVersionError

        flow = _make_flow()
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        with patch(
            "tuya_cloudless.protocol.encode_heartbeat",
            side_effect=UnsupportedVersionError("bad"),
        ):
            # Must not raise
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="bad"
            )
        assert errors == {}


# ── _validate_local_key — OSError on reader.read (lines 1240-1242) ───────────


class TestValidateLocalKeyReadOsError:
    """OSError during reader.read (e.g. connection reset) must return {} — not an error."""

    @pytest.mark.asyncio
    async def test_oserror_on_read_returns_empty(self) -> None:
        """An OSError while reading the heartbeat response is inconclusive → {}."""
        flow = _make_flow()
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        heartbeat_bytes = b"\x00\x00U\xaa" + b"\x00" * 20

        async def fake_wait_for(coro: Any, timeout: Any) -> Any:
            raise OSError("connection reset by peer")

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=heartbeat_bytes,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
        ):
            result = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_oserror_on_read_does_not_propagate(self) -> None:
        """OSError from asyncio.wait_for (reader.read path) must never propagate."""
        flow = _make_flow()
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        heartbeat_bytes = b"\x00\x00U\xaa" + b"\x00" * 20

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=heartbeat_bytes,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=OSError("network unreachable"),
            ),
        ):
            # Must not raise
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {}

    @pytest.mark.asyncio
    async def test_connection_error_subclass_also_handled(self) -> None:
        """ConnectionResetError (an OSError subclass) must be caught as well."""
        flow = _make_flow()
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.get_extra_info = MagicMock(return_value=("10.0.0.1", 6668))

        heartbeat_bytes = b"\x00\x00U\xaa" + b"\x00" * 20

        with (
            patch(
                "tuya_cloudless.protocol.encode_heartbeat",
                return_value=heartbeat_bytes,
            ),
            patch(
                "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
                side_effect=ConnectionResetError("reset"),
            ),
        ):
            errors = await flow._validate_local_key(
                reader, writer, local_key="0123456789abcdef", version="3.3"
            )

        assert errors == {}


# ── async_step_zeroconf (PLAT-766) ────────────────────────────────────────────


class TestAsyncStepZeroconf:
    """Tests for async_step_zeroconf — mDNS discovery trigger."""

    def _make_zeroconf_info(
        self,
        *,
        host: str = "10.0.0.30",
        name: str = "gwid_abc._tuya._tcp.local.",
        properties: dict[str, Any] | None = None,
    ) -> MagicMock:
        """Return a mock ZeroconfServiceInfo."""
        info = MagicMock()
        info.host = host
        info.name = name
        info.properties = properties or {}
        return info

    @pytest.mark.asyncio
    async def test_gwid_from_properties_gwId(self) -> None:
        """gw_id must be read from the 'gwId' TXT record when available."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        info = self._make_zeroconf_info(properties={"gwId": "gw_from_txt"})
        await flow.async_step_zeroconf(info)

        assert flow._device[CONF_GW_ID] == "gw_from_txt"
        flow.async_step_discovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gwid_from_properties_deviceId(self) -> None:
        """'deviceId' TXT record is used when 'gwId' is absent."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        info = self._make_zeroconf_info(properties={"deviceId": "dev_from_txt"})
        await flow.async_step_zeroconf(info)

        assert flow._device[CONF_GW_ID] == "dev_from_txt"

    @pytest.mark.asyncio
    async def test_gwid_fallback_to_service_name_label(self) -> None:
        """gw_id must be inferred from the first DNS label of the service name when
        no TXT record is available."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        info = self._make_zeroconf_info(
            name="mygwid123._tuya._tcp.local.", properties={}
        )
        await flow.async_step_zeroconf(info)

        assert flow._device[CONF_GW_ID] == "mygwid123"

    @pytest.mark.asyncio
    async def test_no_gwid_aborts(self) -> None:
        """When no gw_id can be determined the flow must abort with 'no_device_id'."""
        flow = _make_flow()

        info = self._make_zeroconf_info(name="", properties={})
        # Override name so the split produces an empty first label
        info.name = ""
        await flow.async_step_zeroconf(info)

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_device_dict_contains_host(self) -> None:
        """The device dict must store the IP host from the zeroconf info."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        info = self._make_zeroconf_info(
            host="192.168.1.99", properties={"gwId": "gw_zc_host"}
        )
        await flow.async_step_zeroconf(info)

        assert flow._device[CONF_IP_ADDRESS] == "192.168.1.99"

    @pytest.mark.asyncio
    async def test_version_from_properties(self) -> None:
        """Protocol version must be read from the 'version' TXT record."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        info = self._make_zeroconf_info(properties={"gwId": "gw_v", "version": "3.4"})
        await flow.async_step_zeroconf(info)

        assert flow._device[CONF_PROTOCOL_VERSION] == "3.4"

    @pytest.mark.asyncio
    async def test_version_defaults_when_absent(self) -> None:
        """Protocol version must fall back to DEFAULT_PROTOCOL_VERSION."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        info = self._make_zeroconf_info(properties={"gwId": "gw_def_v"})
        await flow.async_step_zeroconf(info)

        assert flow._device[CONF_PROTOCOL_VERSION] == DEFAULT_PROTOCOL_VERSION

    @pytest.mark.asyncio
    async def test_title_placeholder_set(self) -> None:
        """context['title_placeholders'] must be set to {'name': gw_id}."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        info = self._make_zeroconf_info(properties={"gwId": "gw_title"})
        await flow.async_step_zeroconf(info)

        assert flow.context["title_placeholders"] == {"name": "gw_title"}


# ── async_step_dhcp (PLAT-766) ────────────────────────────────────────────────


class TestAsyncStepDhcp:
    """Tests for async_step_dhcp — DHCP discovery trigger."""

    def _make_dhcp_info(self, *, ip: str = "10.0.0.40") -> MagicMock:
        """Return a mock DhcpServiceInfo."""
        info = MagicMock()
        info.ip = ip
        info.hostname = "tuya-device"
        info.macaddress = "68:57:2D:AA:BB:CC"
        return info

    @pytest.mark.asyncio
    async def test_matching_device_proceeds_to_discovery(self) -> None:
        """When UDP discovery finds the DHCP device, the flow advances to async_step_discovery."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        discovered_device = {
            CONF_GW_ID: "gw_dhcp_01",
            CONF_IP_ADDRESS: "10.0.0.40",
            CONF_PROTOCOL_VERSION: "3.3",
            "product_key": "pk_dhcp",
            "encrypt": True,
        }

        with patch.object(
            flow,
            "_run_discovery",
            new=AsyncMock(return_value=[discovered_device]),
        ):
            await flow.async_step_dhcp(self._make_dhcp_info(ip="10.0.0.40"))

        assert flow._device[CONF_GW_ID] == "gw_dhcp_01"
        flow.async_step_discovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_matching_ip_aborts(self) -> None:
        """When no discovered device matches the DHCP IP, the flow aborts."""
        flow = _make_flow()

        with patch.object(
            flow,
            "_run_discovery",
            new=AsyncMock(return_value=[]),  # nothing discovered
        ):
            await flow.async_step_dhcp(self._make_dhcp_info(ip="10.0.0.41"))

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_discovery_timeout_aborts(self) -> None:
        """TimeoutError from _run_discovery must result in abort with 'no_device_id'."""
        import asyncio as _asyncio

        flow = _make_flow()

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=TimeoutError(),
        ):
            await flow.async_step_dhcp(self._make_dhcp_info())

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_discovery_oserror_aborts(self) -> None:
        """OSError from _run_discovery (via wait_for) must abort with 'no_device_id'."""
        flow = _make_flow()

        with patch(
            "custom_components.tuya_cloudless.config_flow.asyncio.wait_for",
            side_effect=OSError("bind failed"),
        ):
            await flow.async_step_dhcp(self._make_dhcp_info())

        flow.async_abort.assert_called_once_with(reason="no_device_id")

    @pytest.mark.asyncio
    async def test_title_placeholder_set(self) -> None:
        """context['title_placeholders'] must be populated with the gw_id."""
        flow = _make_flow()
        flow.async_step_discovery = AsyncMock(return_value={"type": "form"})

        discovered_device = {
            CONF_GW_ID: "gw_dhcp_02",
            CONF_IP_ADDRESS: "10.0.0.42",
            CONF_PROTOCOL_VERSION: "3.3",
            "product_key": None,
            "encrypt": False,
        }

        with patch.object(
            flow,
            "_run_discovery",
            new=AsyncMock(return_value=[discovered_device]),
        ):
            await flow.async_step_dhcp(self._make_dhcp_info(ip="10.0.0.42"))

        assert flow.context["title_placeholders"] == {"name": "gw_dhcp_02"}
