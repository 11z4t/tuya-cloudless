"""Config flow for Tuya Cloudless integration.

Four-step guided setup (happy path):
  1. async_step_user       — Scan the local network for Tuya devices.
  2. async_step_select     — Choose a discovered device.
  3. async_step_local_key  — Enter the device local key and choose a profile.
  4. async_step_confirm    — Review details, test connection, and save.

Manual fallback:
  async_step_manual        — Enter all device details by hand.

HA-initiated discovery:
  async_step_discovery     — Called when HA auto-detects a Tuya device.
  async_step_zeroconf      — Called when HA sees a _tuya._tcp.local. mDNS record.
  async_step_dhcp          — Called when HA sees a DHCP lease from a known Tuya MAC OUI.

Re-authentication:
  async_step_reauth        — Triggered when the security key is rejected.
  async_step_reauth_confirm — Update the key and test the new connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import ipaddress
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

if TYPE_CHECKING:
    from homeassistant.components.dhcp import DhcpServiceInfo
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .const import (
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
    DEFAULT_OPT_COMMAND_TIMEOUT,
    DEFAULT_OPT_HEARTBEAT_INTERVAL,
    DEFAULT_OPT_RECONNECT_MAX_DELAY,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_TCP_PORT,
    DOMAIN,
    PROFILES_DIR,
    PROTOCOL_VERSIONS,
)

_LOGGER = logging.getLogger(__name__)

_LOCAL_KEY_LENGTH = 16
_DISCOVERY_LISTEN_SECS = 5.0
_DISCOVERY_TIMEOUT = _DISCOVERY_LISTEN_SECS + 1.0
_CONNECTION_TIMEOUT = 3.0
_KEY_VALIDATION_TIMEOUT = 4.0

#: Sentinel profile name meaning "detect automatically from device DP_QUERY response"
_PROFILE_AUTO = "__auto_detect__"


def _validate_ip(ip: str) -> bool:
    """Return True if *ip* is a valid IPv4 or IPv6 address, False otherwise."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _get_profile_options() -> list[SelectOptionDict]:
    """Return profile select options, loading profiles if needed.

    The "Auto-detect" option is always first so users who don't know their
    device type can let the integration detect it automatically (PLAT-778).

    Returns:
        List of :class:`SelectOptionDict` with profile names as values.
    """
    try:
        from tuya_cloudless.profiles import init_profiles, list_profiles

        profiles = list_profiles()
        if not profiles:
            init_profiles(PROFILES_DIR)
            profiles = list_profiles()
    except ImportError:
        profiles = []

    options: list[SelectOptionDict] = [
        SelectOptionDict(value=_PROFILE_AUTO, label="Auto-detect (recommended)"),
    ]
    if not profiles:
        options.append(SelectOptionDict(value="Generic Switch", label="Generic Switch"))
    else:
        options.extend(SelectOptionDict(value=p.name, label=p.name) for p in profiles)
    return options


@functools.lru_cache(maxsize=64)
def _suggest_profile(product_key: str | None) -> str:
    """Return the best-matching profile name for a discovered product key (PLAT-724).

    If a specific (non-wildcard) profile matches the product key, suggest it
    so the user sees their device pre-selected.  Otherwise default to the
    ``__auto_detect__`` sentinel so the integration detects the profile
    automatically at confirm time (PLAT-778).

    Results are cached by product_key — the profile list is static at runtime
    so repeated calls for the same key (across multiple config flow steps) are
    free after the first lookup.

    Args:
        product_key: Product key from UDP device discovery, or ``None``.

    Returns:
        Profile name string to use as the form default.
    """
    if product_key:
        try:
            from tuya_cloudless.profiles import find_profile_by_product_key

            match = find_profile_by_product_key(product_key)
            # Only suggest a specific profile when it has a non-wildcard model
            # pattern (i.e. it's a known device, not just a generic fallback).
            if match is not None and match.model != "*":
                return match.name
        except ImportError:
            pass

    return _PROFILE_AUTO


class TuyaCloudlessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Tuya Cloudless config flow.

    Provides guided setup with automatic device discovery and a manual
    fallback for users who have device details available.
    """

    VERSION = CONFIG_ENTRY_VERSION
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._https_unavailable: bool = False
        self._discovered: list[dict[str, Any]] = []
        self._device: dict[str, Any] = {}
        self._manual_ha_url: str | None = None
        self._wifi_ap_http_url: str | None = None  # HTTP URL saved for WiFi-AP fallback

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_https_from_request(server: Any) -> str | None:
        """Try to read the HTTPS base URL from the active browser request.

        The config flow API call is made by the HA frontend, so the aiohttp
        request that triggered it originates from the same browser session.
        If the request carries ``X-Forwarded-Proto: https`` (reverse proxy) or
        its own scheme is ``https``, we can build the pairing URL from the
        ``Host`` header — no manual input required.

        Args:
            server: Running :class:`PairingServer` instance (used for fallback).

        Returns:
            Absolute pairing URL string starting with ``https://``, or
            ``None`` when no HTTPS request context is available.
        """
        try:
            from homeassistant.helpers.http import current_request

            request = current_request.get()
            if request is None:
                return None
            proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
            if proto != "https":
                return None
            host = request.headers.get("X-Forwarded-Host", request.host)
            return f"https://{host}/api/tuya_cloudless/pairing"
        except Exception:  # broad catch — must never crash the flow
            return None

    # ── Step 0: Pairing tool deep-link ─────────────────────────────────────────

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Import device data pre-filled from the Tuya Cloudless pairing tool.

        The pairing tool (served at ``http://ha-host:8099``) links back to HA
        with ``gw_id``, ``local_key``, and ``ip_address`` as URL query params.
        The config flow framework passes these as ``user_input`` on the first
        call so the user only has to confirm, not retype credentials.

        Args:
            user_input: Pre-filled device dict from the pairing tool, or
                ``None`` on initial render.

        Returns:
            Config flow result — skips to confirm step if data is valid,
            otherwise falls through to the manual entry form.
        """
        if user_input is not None:
            gw_id = str(user_input.get(CONF_GW_ID, "")).strip()
            local_key = str(user_input.get(CONF_LOCAL_KEY, "")).strip()
            ip_address = str(user_input.get(CONF_IP_ADDRESS, "")).strip()

            if (
                gw_id
                and len(local_key) == _LOCAL_KEY_LENGTH
                and local_key.isascii()
                and local_key.isprintable()
                and _validate_ip(ip_address)
            ):
                self._device = {
                    CONF_GW_ID: gw_id,
                    CONF_LOCAL_KEY: local_key,
                    CONF_IP_ADDRESS: ip_address,
                    CONF_PROTOCOL_VERSION: str(
                        user_input.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION)
                    ),
                    CONF_DEVICE_NAME: str(user_input.get(CONF_DEVICE_NAME, "")).strip()
                    or ip_address,
                }
                return await self.async_step_local_key()

        # Data missing or invalid — fall through to manual entry
        return await self.async_step_manual()

    # ── BLE Pairing ────────────────────────────────────────────────────────────

    async def async_step_ble_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Launch the BLE pairing tool in the user's browser and wait for activation.

        First call (``user_input`` is ``None``):
          - Starts the pairing server on port 8099 (if not already running).
          - Registers this flow so the server can resume it on device activation.
          - Returns an :meth:`async_external_step` pointing the browser to 8099.

        Second call (``user_input`` contains device data from the server):
          - Stores device details and advances to :meth:`async_step_ble_confirm`.

        Args:
            user_input: ``None`` on first render; device data dict on activation.

        Returns:
            Config flow result — external step or advance to confirm.
        """
        if user_input is not None:
            # Second call: pairing server resumed us with device data.
            self._device = {
                CONF_GW_ID: str(user_input.get(CONF_GW_ID, "")).strip(),
                CONF_LOCAL_KEY: str(user_input.get(CONF_LOCAL_KEY, "")).strip(),
                CONF_IP_ADDRESS: str(user_input.get("ip_address", "")).strip(),
                "product_key": str(user_input.get("product_key", "")).strip(),
            }
            return self.async_external_step_done(next_step_id="ble_confirm")

        # First call: start the pairing server and open the browser.
        from .pairing_server import ensure_pairing_server

        try:
            server = await ensure_pairing_server(self.hass)
        except OSError as exc:
            _LOGGER.warning("Could not start pairing server: %s", exc)
            return self.async_abort(reason="pairing_server_unavailable")

        # PLAT-809: Require HTTPS for non-localhost URLs so that the browser
        # can use Web Bluetooth (which requires a secure context).
        from homeassistant.helpers.network import NoURLAvailableError, get_url

        # If the user provided a manual HTTPS URL, use it directly and skip
        # automatic detection.  The pairing views are mounted under the same
        # prefix as the auto-detected path.
        if self._manual_ha_url is not None:
            pairing_url = self._manual_ha_url.rstrip("/") + "/api/tuya_cloudless/pairing"
        else:
            # PLAT-870: Try to detect HTTPS from the browser's own request first.
            # The config flow API call comes from the same browser session, so the
            # request URL/headers reflect what scheme the user is actually using.
            pairing_url = self._detect_https_from_request(server) or server.ha_ui_url()
            _parsed_pairing = urlparse(pairing_url)
            _hostname = _parsed_pairing.hostname or ""
            _is_localhost = _hostname in ("localhost", "127.0.0.1")

            if not _is_localhost and not pairing_url.startswith("https://"):
                # Fall back to HA network helpers as a secondary check.
                _has_https = False
                for _kwargs in (
                    {"allow_internal": True, "allow_external": False},
                    {"allow_internal": False, "allow_external": True},
                ):
                    try:
                        _url = get_url(self.hass, **_kwargs)
                        if _url.startswith("https://"):
                            _has_https = True
                            pairing_url = _url.rstrip("/") + "/api/tuya_cloudless/pairing"
                            break
                    except NoURLAvailableError:
                        continue

                if not _has_https:
                    self._https_unavailable = True
                    # Save the HTTP URL so ble_fallback can offer WiFi AP pairing.
                    # WiFi AP provisioning does not require a secure context — only
                    # Web Bluetooth does.  The pairing UI shows its own BLE warning.
                    self._wifi_ap_http_url = pairing_url
                    return await self.async_step_ble_fallback()

        server.register_flow(self.flow_id)

        # Point the external step directly at the pairing server.
        # ha_local_url() resolves the best available hostname with three
        # fallbacks and always returns a non-empty string, so no additional
        # URL-resolution step is needed.
        url = f"{pairing_url}/?flow_id={self.flow_id}"
        return self.async_external_step(
            step_id="ble_pair",
            url=url,
        )

    async def async_step_ble_fallback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """HTTPS not available — offer Search or Manual setup as alternatives.

        Shown instead of aborting when the HTTPS check fails so the user can
        still add a device that is already on their network.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result — advances to search or manual setup.
        """
        if user_input is not None:
            if user_input.get("setup_mode") == "wifi_ap":
                # Open the pairing UI over HTTP — WiFi AP pairing works without HTTPS.
                # The pairing UI disables BLE and shows a hint explaining why.
                from .pairing_server import ensure_pairing_server, get_pairing_server

                try:
                    server = await ensure_pairing_server(self.hass)
                except OSError:
                    server = None
                if server is None:
                    server = get_pairing_server(self.hass)
                if server is not None:
                    server.register_flow(self.flow_id)
                    pairing_url = self._wifi_ap_http_url or server.ha_ui_url()
                    url = f"{pairing_url}/?flow_id={self.flow_id}"
                    return self.async_external_step(step_id="ble_pair", url=url)
                # Pairing server unavailable — fall back to manual
                return await self.async_step_manual()
            if user_input.get("setup_mode") == "manual":
                return await self.async_step_manual()
            if user_input.get("setup_mode") == "provide_url":
                return await self.async_step_ble_ha_url()
            # Default: scan network — run discovery then go to select step
            try:
                self._discovered = await asyncio.wait_for(
                    self._run_discovery(),
                    timeout=_DISCOVERY_TIMEOUT,
                )
            except (TimeoutError, OSError):
                self._discovered = []
            return await self.async_step_select()

        schema = vol.Schema(
            {
                vol.Required("setup_mode", default="wifi_ap"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value="wifi_ap",
                                label="Pair a new device via WiFi (device in setup mode)",
                            ),
                            SelectOptionDict(value="scan", label="Search for device on network"),
                            SelectOptionDict(value="manual", label="Enter device details manually"),
                            SelectOptionDict(
                                value="provide_url",
                                label="Enter my Home Assistant HTTPS URL (for BLE pairing)",
                            ),
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="ble_fallback",
            data_schema=schema,
        )

    async def async_step_ble_ha_url(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user type their HA HTTPS URL to enable BLE pairing.

        Shown when automatic HTTPS detection fails so the user can override.
        The provided URL must start with ``https://`` to guarantee a secure
        context for Web Bluetooth.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result — proceeds to BLE pairing on a valid URL.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_url = str(user_input.get("ha_url", "")).strip().rstrip("/")
            if not raw_url.startswith("https://"):
                errors["ha_url"] = "invalid_ha_url_https"
            else:
                self._manual_ha_url = raw_url
                return await self.async_step_ble_pair()

        schema = vol.Schema(
            {vol.Required("ha_url"): TextSelector(TextSelectorConfig(type=TextSelectorType.URL))}
        )
        return self.async_show_form(
            step_id="ble_ha_url",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_ble_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user name the newly paired device and select a profile.

        The device ``gw_id``, ``local_key``, and ``ip_address`` were filled in
        by :meth:`async_step_ble_pair` from the pairing server callback.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result — creates the entry on success.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            gw_id = self._device.get(CONF_GW_ID, "")
            ip_address = self._device.get(CONF_IP_ADDRESS, "")
            local_key = self._device.get(CONF_LOCAL_KEY, "")
            name = (user_input.get(CONF_DEVICE_NAME) or "").strip() or ip_address

            from .pairing_server import get_pairing_server

            server = get_pairing_server(self.hass)
            if server is not None:
                server.unregister_flow(self.flow_id)

            await self.async_set_unique_id(gw_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={
                    CONF_GW_ID: gw_id,
                    CONF_LOCAL_KEY: local_key,
                    CONF_IP_ADDRESS: ip_address,
                    CONF_PROTOCOL_VERSION: DEFAULT_PROTOCOL_VERSION,
                    CONF_PROFILE: user_input.get(CONF_PROFILE, "Generic Switch"),
                    CONF_DEVICE_NAME: name,
                },
            )

        profile_options = _get_profile_options()
        default_profile = _suggest_profile(self._device.get("product_key"))
        default_name = self._device.get(CONF_IP_ADDRESS, "")

        schema = vol.Schema(
            {
                vol.Optional(CONF_DEVICE_NAME, default=default_name): str,
                vol.Optional(CONF_PROFILE, default=default_profile): SelectSelector(
                    SelectSelectorConfig(
                        options=profile_options,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="ble_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "ip_address": self._device.get(CONF_IP_ADDRESS, ""),
                "gw_id": self._device.get(CONF_GW_ID, ""),
            },
        )

    # ── Step 1: Search ─────────────────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: Jump straight to BLE pairing (the primary setup path).

        The BLE pairing web tool at port 8099 handles WiFi credential entry
        and local-key provisioning in a single browser flow.  Advanced users
        who already have their device on the network can reach the manual
        entry path via :meth:`async_step_manual` directly.

        Args:
            user_input: Unused — kept for HA config-flow protocol compatibility.

        Returns:
            Config flow result — external step pointing to the pairing UI.
        """
        return await self.async_step_ble_pair()

    # ── Step 2: Select ─────────────────────────────────────────────────────────

    async def async_step_select(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 2: Choose one of the discovered devices.

        Shows a list of devices found on the local network, labelled with
        their IP address and firmware version. A manual-entry option is
        always available at the bottom of the list.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result directing to the next step.
        """
        if not self._discovered:
            return await self.async_step_manual()

        if user_input is not None:
            gw_id: str = user_input["device"]

            if gw_id == "__manual__":
                return await self.async_step_manual()

            matches = [d for d in self._discovered if d[CONF_GW_ID] == gw_id]
            if matches:
                self._device = matches[0]
                return await self.async_step_local_key()

        device_options: list[SelectOptionDict] = [
            SelectOptionDict(
                value=d[CONF_GW_ID],
                label=f"{d[CONF_IP_ADDRESS]}  — firmware {d[CONF_PROTOCOL_VERSION]}",
            )
            for d in self._discovered
        ]
        device_options.append(SelectOptionDict(value="__manual__", label="Add a device manually…"))

        schema = vol.Schema(
            {
                vol.Required("device"): SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="select",
            data_schema=schema,
            description_placeholders={"count": str(len(self._discovered))},
        )

    # ── Step 3: Local key + profile ────────────────────────────────────────────

    async def async_step_local_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Enter the security key and choose a device profile.

        The local key is a 16-character string generated during pairing.
        The profile determines which controls appear in Home Assistant.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result directing to the next step.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            local_key = user_input[CONF_LOCAL_KEY].strip()
            if len(local_key) != _LOCAL_KEY_LENGTH:
                errors[CONF_LOCAL_KEY] = "invalid_local_key"
            elif not (local_key.isascii() and local_key.isprintable()):
                errors[CONF_LOCAL_KEY] = "invalid_local_key_chars"
            else:
                name = (user_input.get(CONF_DEVICE_NAME) or "").strip()
                self._device.update(
                    {
                        CONF_LOCAL_KEY: local_key,
                        CONF_DEVICE_NAME: name or self._device.get(CONF_IP_ADDRESS, "Tuya device"),
                        CONF_PROFILE: user_input.get(CONF_PROFILE, "Generic Switch"),
                    }
                )
                return await self.async_step_confirm()

        profile_options = _get_profile_options()
        default_profile = _suggest_profile(self._device.get("product_key"))

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCAL_KEY): str,
                vol.Optional(CONF_DEVICE_NAME, default=""): str,
                vol.Optional(CONF_PROFILE, default=default_profile): SelectSelector(
                    SelectSelectorConfig(
                        options=profile_options,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="local_key",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "ip_address": self._device.get(CONF_IP_ADDRESS, ""),
                "firmware": self._device.get(CONF_PROTOCOL_VERSION, ""),
                "pairing_tool_url": self._pairing_tool_url(),
            },
        )

    # ── Step 4: Confirm ────────────────────────────────────────────────────────

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: Review the device details and verify connectivity.

        Performs a brief TCP check to confirm the device is reachable before
        saving the config entry.

        Args:
            user_input: Empty dict when the user clicks Confirm, or ``None``
                on first render.

        Returns:
            Config flow result — creates the entry if connectivity passes.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            gw_id = self._device[CONF_GW_ID]
            ip_address = self._device[CONF_IP_ADDRESS]
            dev_local_key = self._device.get(CONF_LOCAL_KEY, "")
            dev_version = self._device.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION)

            errors = await self._check_connection(
                ip_address, local_key=dev_local_key, version=dev_version
            )

            if not errors:
                # Auto-detect profile from device DP_QUERY response (PLAT-778).
                profile = self._device.get(CONF_PROFILE, "Generic Switch")
                if profile == _PROFILE_AUTO:
                    profile = await self._auto_detect_profile(
                        ip_address,
                        local_key=dev_local_key,
                        version=dev_version,
                    )
                    self._device[CONF_PROFILE] = profile

                await self.async_set_unique_id(gw_id)
                self._abort_if_unique_id_configured()
                title = self._device.get(CONF_DEVICE_NAME) or ip_address
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_GW_ID: gw_id,
                        CONF_LOCAL_KEY: self._device[CONF_LOCAL_KEY],
                        CONF_IP_ADDRESS: ip_address,
                        CONF_PROTOCOL_VERSION: dev_version,
                        CONF_PROFILE: profile,
                        CONF_DEVICE_NAME: title,
                    },
                )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "device_name": self._device.get(CONF_DEVICE_NAME, ""),
                "ip_address": self._device.get(CONF_IP_ADDRESS, ""),
            },
        )

    # ── Manual fallback ────────────────────────────────────────────────────────

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual device setup — all fields in a single form.

        For users who have device details available and do not need the
        automatic discovery flow.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result — creates the entry on success.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            gw_id = user_input[CONF_GW_ID].strip()
            local_key = user_input[CONF_LOCAL_KEY].strip()
            ip_address = user_input[CONF_IP_ADDRESS].strip()

            if not gw_id or len(gw_id) > 64:
                errors[CONF_GW_ID] = "invalid_gw_id"
            if len(local_key) != _LOCAL_KEY_LENGTH:
                errors[CONF_LOCAL_KEY] = "invalid_local_key"
            elif not (local_key.isascii() and local_key.isprintable()):
                errors[CONF_LOCAL_KEY] = "invalid_local_key_chars"
            if not _validate_ip(ip_address):
                errors[CONF_IP_ADDRESS] = "invalid_ip_address"

            if not errors:
                manual_version = user_input.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION)
                errors = await self._check_connection(
                    ip_address, local_key=local_key, version=manual_version
                )

            if not errors:
                await self.async_set_unique_id(gw_id)
                self._abort_if_unique_id_configured()
                title = (user_input.get(CONF_DEVICE_NAME) or "").strip() or ip_address
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_GW_ID: gw_id,
                        CONF_LOCAL_KEY: local_key,
                        CONF_IP_ADDRESS: ip_address,
                        CONF_PROTOCOL_VERSION: user_input.get(
                            CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
                        ),
                        CONF_PROFILE: user_input.get(CONF_PROFILE, "Generic Switch"),
                        CONF_DEVICE_NAME: title,
                    },
                )

        profile_options = _get_profile_options()
        default_profile = _suggest_profile(self._device.get("product_key"))

        schema = vol.Schema(
            {
                vol.Required(CONF_GW_ID): str,
                vol.Required(CONF_LOCAL_KEY): str,
                vol.Required(CONF_IP_ADDRESS): str,
                vol.Optional(CONF_PROTOCOL_VERSION, default=DEFAULT_PROTOCOL_VERSION): vol.In(
                    PROTOCOL_VERSIONS
                ),
                vol.Optional(CONF_PROFILE, default=default_profile): SelectSelector(
                    SelectSelectorConfig(
                        options=profile_options,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(CONF_DEVICE_NAME, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "pairing_tool_url": self._pairing_tool_url(),
            },
        )

    # ── HA-initiated discovery ─────────────────────────────────────────────────

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setup after Home Assistant auto-detected a Tuya device.

        Called by the HA discovery framework when a Tuya device is detected
        via passive network monitoring. ``self._device`` must be populated
        with at minimum :const:`CONF_GW_ID` and :const:`CONF_IP_ADDRESS`
        before this step is called.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            local_key = user_input[CONF_LOCAL_KEY].strip()
            if len(local_key) != _LOCAL_KEY_LENGTH:
                errors[CONF_LOCAL_KEY] = "invalid_local_key"
            elif not (local_key.isascii() and local_key.isprintable()):
                errors[CONF_LOCAL_KEY] = "invalid_local_key_chars"
            else:
                name = (user_input.get(CONF_DEVICE_NAME) or "").strip()
                self._device.update(
                    {
                        CONF_LOCAL_KEY: local_key,
                        CONF_DEVICE_NAME: name or self._device.get(CONF_IP_ADDRESS, "Tuya device"),
                        CONF_PROFILE: user_input.get(CONF_PROFILE, "Generic Switch"),
                    }
                )
                return await self.async_step_confirm()

        profile_options = _get_profile_options()
        default_profile = _suggest_profile(self._device.get("product_key"))

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCAL_KEY): str,
                vol.Optional(CONF_DEVICE_NAME, default=""): str,
                vol.Optional(CONF_PROFILE, default=default_profile): SelectSelector(
                    SelectSelectorConfig(
                        options=profile_options,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="discovery",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "ip_address": self._device.get(CONF_IP_ADDRESS, ""),
                "firmware": self._device.get(CONF_PROTOCOL_VERSION, ""),
                "pairing_tool_url": self._pairing_tool_url(),
            },
        )

    # ── Zeroconf / DHCP auto-discovery (PLAT-766) ─────────────────────────────

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle mDNS/zeroconf discovery of a ``_tuya._tcp.local.`` service.

        Home Assistant calls this automatically when a device advertises the
        ``_tuya._tcp.local.`` mDNS service type.  The device gateway ID is
        extracted from the TXT record ``gwId``/``deviceId`` field, or falls
        back to the first label of the service name (e.g.
        ``<gwId>._tuya._tcp.local.``).

        The user still needs to provide the security key, so this step
        forwards to :meth:`async_step_discovery` after populating
        ``self._device``.

        Args:
            discovery_info: mDNS service info provided by HA.

        Returns:
            Config flow result.
        """
        host = discovery_info.host
        props = discovery_info.properties

        gw_id: str | None = props.get("gwId") or props.get("deviceId")
        if not gw_id:
            name_part = discovery_info.name.split(".")[0]
            gw_id = name_part if name_part else None
        if not gw_id:
            return self.async_abort(reason="no_device_id")

        await self.async_set_unique_id(gw_id)
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: host})

        self._device = {
            CONF_GW_ID: gw_id,
            CONF_IP_ADDRESS: host,
            CONF_PROTOCOL_VERSION: props.get("version", DEFAULT_PROTOCOL_VERSION),
            "product_key": props.get("productKey") or props.get("product_key"),
        }
        self.context["title_placeholders"] = {"name": gw_id}
        return await self.async_step_discovery()

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle DHCP discovery of a device with a known Tuya MAC OUI.

        Home Assistant calls this when a DHCP lease is seen for a MAC address
        matching one of the OUI prefixes listed in ``manifest.json``.  The IP
        address is known but the gateway ID is not, so a UDP broadcast scan is
        run to identify the device.

        Args:
            discovery_info: DHCP service info (ip, hostname, macaddress).

        Returns:
            Config flow result.
        """
        host = discovery_info.ip
        try:
            discovered = await asyncio.wait_for(
                self._run_discovery(),
                timeout=_DISCOVERY_TIMEOUT,
            )
        except (TimeoutError, OSError):
            return self.async_abort(reason="no_device_id")

        device = next(
            (d for d in discovered if d[CONF_IP_ADDRESS] == host),
            None,
        )
        if device is None:
            return self.async_abort(reason="no_device_id")

        gw_id = device[CONF_GW_ID]
        await self.async_set_unique_id(gw_id)
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: host})

        self._device = device
        self.context["title_placeholders"] = {"name": gw_id}
        return await self.async_step_discovery()

    # ── Re-authentication ──────────────────────────────────────────────────────

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Initiate re-authentication when the security key is rejected.

        This step is triggered automatically by the coordinator when the
        device refuses the current key (e.g. after factory reset or
        re-pairing via the Tuya app).

        Args:
            entry_data: Current config entry data dict.

        Returns:
            Config flow result showing the re-auth form.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the re-authentication form submission.

        Validates the new security key and tests the connection before
        updating the config entry.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result — updates the entry on success.
        """
        errors: dict[str, str] = {}

        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            local_key = user_input[CONF_LOCAL_KEY].strip()
            if len(local_key) != _LOCAL_KEY_LENGTH:
                errors[CONF_LOCAL_KEY] = "invalid_local_key"
            elif not (local_key.isascii() and local_key.isprintable()):
                errors[CONF_LOCAL_KEY] = "invalid_local_key_chars"
            else:
                ip_address = user_input.get(CONF_IP_ADDRESS, "").strip() or reauth_entry.data.get(
                    CONF_IP_ADDRESS, ""
                )
                if ip_address and not _validate_ip(ip_address):
                    errors[CONF_IP_ADDRESS] = "invalid_ip_address"
                else:
                    reauth_version = reauth_entry.data.get(
                        CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
                    )
                    errors = await self._check_connection(
                        ip_address, local_key=local_key, version=reauth_version
                    )

                if not errors:
                    new_data = {
                        **reauth_entry.data,
                        CONF_LOCAL_KEY: local_key,
                        CONF_IP_ADDRESS: ip_address,
                    }
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data=new_data,
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCAL_KEY): str,
                vol.Optional(
                    CONF_IP_ADDRESS,
                    default=reauth_entry.data.get(CONF_IP_ADDRESS, ""),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "device_name": reauth_entry.title,
                "pairing_tool_url": self._pairing_tool_url(),
            },
        )

    # ── Reconfigure flow ───────────────────────────────────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to update IP address or local key without removing the device.

        Unlike re-auth (which is triggered automatically on key rejection), reconfigure
        is user-initiated — useful when the device gets a new IP after DHCP renewal.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result — updates and reloads the entry on success.
        """
        reconfigure_entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            local_key = user_input[CONF_LOCAL_KEY].strip()
            ip_address = user_input[CONF_IP_ADDRESS].strip()

            if len(local_key) != _LOCAL_KEY_LENGTH:
                errors[CONF_LOCAL_KEY] = "invalid_local_key"
            elif not (local_key.isascii() and local_key.isprintable()):
                errors[CONF_LOCAL_KEY] = "invalid_local_key_chars"
            elif not _validate_ip(ip_address):
                errors[CONF_IP_ADDRESS] = "invalid_ip_address"
            else:
                reconfig_version = reconfigure_entry.data.get(
                    CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
                )
                errors = await self._check_connection(
                    ip_address, local_key=local_key, version=reconfig_version
                )

            if not errors:
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data={
                        **reconfigure_entry.data,
                        CONF_LOCAL_KEY: local_key,
                        CONF_IP_ADDRESS: ip_address,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_IP_ADDRESS,
                    default=reconfigure_entry.data.get(CONF_IP_ADDRESS, ""),
                ): str,
                # local_key is a secret — intentionally no default so the key is
                # never pre-filled in the form or transmitted in the form JSON response.
                vol.Required(CONF_LOCAL_KEY): str,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "device_name": reconfigure_entry.title,
            },
        )

    # ── Options flow ───────────────────────────────────────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> TuyaCloudlessOptionsFlow:
        """Return the options flow handler."""
        return TuyaCloudlessOptionsFlow()

    async def async_remove(self) -> None:
        """Clean up when the flow is cancelled or removed.

        Unregisters the flow from the pairing server so the idle-stop timer
        can eventually shut the server down.
        """
        from .pairing_server import get_pairing_server

        server = get_pairing_server(self.hass)
        if server is not None:
            server.unregister_flow(self.flow_id)

    def _pairing_tool_url(self) -> str:
        """Return the URL to the Tuya Cloudless pairing tool running on this HA host.

        Uses the same host-resolution chain as the pairing server so the link
        works regardless of how the user reaches Home Assistant.

        Returns:
            Absolute HTTP URL string for the pairing tool.
        """
        from .pairing_server import PAIRING_SERVER_PORT

        # 1. HA network helper
        try:
            from homeassistant.helpers.network import get_url

            base = get_url(self.hass, allow_internal=True, allow_external=False)
            host = urlparse(base).hostname or ""
            if host:
                return f"http://{host}:{PAIRING_SERVER_PORT}"
        except Exception as exc:  # broad catch — must not crash config flow
            _LOGGER.debug("HA network helper unavailable in _resolve_ha_base_url: %s", exc)

        # 2. hass.config.internal_url
        try:
            internal = getattr(self.hass.config, "internal_url", None)
            if isinstance(internal, str) and internal:
                host = urlparse(internal).hostname or ""
                if host:
                    return f"http://{host}:{PAIRING_SERVER_PORT}"
        except Exception as exc:  # broad catch — must not crash config flow
            _LOGGER.debug("internal_url fallback failed in _resolve_ha_base_url: %s", exc)

        # 3. Machine hostname
        import socket

        hostname = socket.getfqdn() or socket.gethostname() or "homeassistant.local"
        return f"http://{hostname}:{PAIRING_SERVER_PORT}"

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _auto_detect_profile(
        self,
        ip_address: str,
        *,
        local_key: str,
        version: str,
    ) -> str:
        """Send a DP_QUERY to the device and infer its profile from the response.

        Opens a fresh TCP connection, sends CMD_DP_QUERY (0x0a), reads the
        response, and matches the observed DP IDs against loaded profiles
        using :func:`~tuya_cloudless.profiles.detect_profile_from_dps`.

        Falls back to ``"Generic Switch"`` on any error or when no profile
        matches the observed DP set (PLAT-778).

        Args:
            ip_address: Device IP address.
            local_key:  16-character device security key.
            version:    Protocol version string (e.g. ``"3.3"``).

        Returns:
            Detected profile name, or ``"Generic Switch"`` if detection fails.
        """
        try:
            from tuya_cloudless.crypto import CryptoError
            from tuya_cloudless.exceptions import MalformedPacketError
            from tuya_cloudless.profiles import (
                detect_profile_from_dps,
                init_profiles,
                list_profiles,
            )
            from tuya_cloudless.protocol import decode_frame, encode_status_query, split_frames
        except ImportError:
            return "Generic Switch"

        key_bytes = local_key.encode("utf-8")

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip_address, DEFAULT_TCP_PORT),
                timeout=_CONNECTION_TIMEOUT,
            )
        except (TimeoutError, OSError):
            return "Generic Switch"

        try:
            try:
                query = encode_status_query(
                    sequence=1,
                    version=version,
                    local_key=key_bytes,
                    session_key=None,
                )
                writer.write(query)
                await writer.drain()
            except (OSError, CryptoError):
                # CryptoError is raised by encode_status_query for v3.4/v3.5 when
                # session_key=None — skip auto-detection for those protocol versions.
                return "Generic Switch"

            try:
                raw = await asyncio.wait_for(reader.read(4096), timeout=_KEY_VALIDATION_TIMEOUT)
            except (TimeoutError, OSError):
                return "Generic Switch"

            if not raw:
                return "Generic Switch"

            frames, _ = split_frames(raw)
            if not frames:
                return "Generic Switch"

            try:
                frame = decode_frame(frames[0], version=version, local_key=key_bytes)
            except (CryptoError, MalformedPacketError, ValueError):
                return "Generic Switch"

            try:
                dps_payload = frame.dps
                if isinstance(dps_payload, dict):
                    raw_dps = dps_payload.get("dps", dps_payload)
                    dp_ids: set[str] = set(raw_dps.keys()) if isinstance(raw_dps, dict) else set()
                else:
                    dp_ids = set()
            except Exception:
                return "Generic Switch"

            if not dp_ids:
                return "Generic Switch"

            # Load profiles if not yet loaded
            profiles = list_profiles()
            if not profiles:
                init_profiles(PROFILES_DIR)
                profiles = list_profiles()

            match = detect_profile_from_dps(dp_ids)
            if match is not None:
                _LOGGER.info(
                    "Auto-detected profile '%s' from DP IDs: %s",
                    match.name,
                    sorted(dp_ids),
                )
                return match.name

            return "Generic Switch"

        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _run_discovery(self) -> list[dict[str, Any]]:
        """Run UDP device discovery and return a list of found device dicts.

        Returns:
            List of dicts with CONF_GW_ID, CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION, ``product_key``, and ``encrypt`` keys.
        """
        try:
            from tuya_cloudless.discovery import DiscoveryListener
        except ImportError:
            _LOGGER.debug("Discovery module not available; skipping scan")
            return []

        listener = DiscoveryListener()
        try:
            await listener.start()
            await asyncio.sleep(_DISCOVERY_LISTEN_SECS)
            raw = listener.get_all()
        except OSError:
            raise
        finally:
            await listener.stop()

        return [
            {
                CONF_GW_ID: dev.gw_id,
                CONF_IP_ADDRESS: dev.ip,
                CONF_PROTOCOL_VERSION: dev.version,
                "product_key": dev.product_key,
                "encrypt": dev.encrypt,
            }
            for dev in raw
        ]

    async def _check_connection(
        self,
        ip_address: str,
        *,
        local_key: str = "",
        version: str = DEFAULT_PROTOCOL_VERSION,
    ) -> dict[str, str]:
        """Try a TCP connect and optionally validate the local key against the device.

        Opens a TCP connection to the device and, when a ``local_key`` is
        provided, sends a heartbeat frame and attempts to decrypt the response.
        A decryption failure means the key is wrong. A timeout waiting for the
        response is treated as OK — the device may simply be slow or running
        firmware that does not send an immediate heartbeat reply.

        Args:
            ip_address: Device IP address.
            local_key: 16-character device security key. When non-empty the
                method attempts to validate the key by exchanging a heartbeat
                frame. Pass an empty string to skip key validation (TCP-only
                check).
            version: Protocol version string used for the heartbeat frame
                (e.g. ``"3.3"``). Defaults to :data:`.const.DEFAULT_PROTOCOL_VERSION`.

        Returns:
            Error dict mapping field name to error key.
            Empty if the connection (and optional key validation) succeeded.
        """
        errors: dict[str, str] = {}
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip_address, DEFAULT_TCP_PORT),
                timeout=_CONNECTION_TIMEOUT,
            )
        except (TimeoutError, OSError) as exc:
            _LOGGER.debug("Connection test to %s failed: %s", ip_address, exc)
            errors[CONF_IP_ADDRESS] = "cannot_connect"
            return errors

        try:
            if local_key and len(local_key) == _LOCAL_KEY_LENGTH:
                errors = await self._validate_local_key(
                    reader, writer, local_key=local_key, version=version
                )
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

        return errors

    async def _validate_local_key(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        local_key: str,
        version: str,
    ) -> dict[str, str]:
        """Send a heartbeat and attempt to decode the response to validate the key.

        This is a best-effort check. A timeout waiting for the device response
        is silently ignored — setup proceeds normally. Only a clear decryption
        failure (wrong PKCS7 padding, bad GCM tag, etc.) causes an error.

        Args:
            reader: Open asyncio stream reader connected to the device.
            writer: Open asyncio stream writer connected to the device.
            local_key: 16-character device security key.
            version: Protocol version string for the heartbeat frame.

        Returns:
            Error dict. Empty on success or timeout. ``{"base": "invalid_auth_key"}``
            on confirmed decryption failure.
        """
        try:
            from tuya_cloudless.crypto import AuthenticationError, CryptoError
            from tuya_cloudless.exceptions import MalformedPacketError, UnsupportedVersionError
            from tuya_cloudless.protocol import decode_frame, encode_heartbeat, split_frames
        except ImportError:
            _LOGGER.debug("Protocol library not available; skipping key validation")
            return {}

        key_bytes = local_key.encode("utf-8")

        try:
            heartbeat = encode_heartbeat(sequence=1, version=version, local_key=key_bytes)
        except (UnsupportedVersionError, CryptoError) as exc:
            _LOGGER.debug("Could not encode heartbeat for key validation: %s", exc)
            return {}

        try:
            writer.write(heartbeat)
            await writer.drain()
        except OSError as exc:
            _LOGGER.debug("Could not send heartbeat during key validation: %s", exc)
            return {}

        try:
            raw = await asyncio.wait_for(reader.read(4096), timeout=_KEY_VALIDATION_TIMEOUT)
        except TimeoutError:
            _LOGGER.debug(
                "Device at %s did not respond to heartbeat — skipping key validation",
                writer.get_extra_info("peername"),
            )
            return {}
        except OSError as exc:
            _LOGGER.debug("Read error during key validation: %s", exc)
            return {}

        if not raw:
            _LOGGER.debug("Device closed connection during key validation — skipping check")
            return {}

        frames, _ = split_frames(raw)
        if not frames:
            _LOGGER.debug("No complete frames in heartbeat response — skipping key validation")
            return {}

        try:
            decode_frame(frames[0], version=version, local_key=key_bytes)
        except (CryptoError, AuthenticationError) as exc:
            _LOGGER.debug("Key validation failed — decryption error: %s", type(exc).__name__)
            return {"base": "invalid_auth_key"}
        except (MalformedPacketError, UnsupportedVersionError, ValueError) as exc:
            _LOGGER.debug("Key validation inconclusive — frame parse error: %s", exc)
            return {}

        return {}


# ── Options flow ───────────────────────────────────────────────────────────────


class TuyaCloudlessOptionsFlow(OptionsFlow):
    """Handle Tuya Cloudless options -- timing and reconnect behaviour only.

    ``ip_address`` and ``protocol_version`` belong to ``entry.data`` and are
    changed via the *Reconfigure* flow. They must NOT appear here (PLAT-715).
    """

    MINOR_VERSION = 1

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result.
        """
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options or {}

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_OPT_HEARTBEAT_INTERVAL,
                    default=int(
                        opts.get(CONF_OPT_HEARTBEAT_INTERVAL, DEFAULT_OPT_HEARTBEAT_INTERVAL)
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                vol.Optional(
                    CONF_OPT_COMMAND_TIMEOUT,
                    default=int(opts.get(CONF_OPT_COMMAND_TIMEOUT, DEFAULT_OPT_COMMAND_TIMEOUT)),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                vol.Optional(
                    CONF_OPT_RECONNECT_MAX_DELAY,
                    default=int(
                        opts.get(CONF_OPT_RECONNECT_MAX_DELAY, DEFAULT_OPT_RECONNECT_MAX_DELAY)
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
