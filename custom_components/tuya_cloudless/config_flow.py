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

Re-authentication:
  async_step_reauth        — Triggered when the security key is rejected.
  async_step_reauth_confirm — Update the key and test the new connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

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


def _get_profile_options() -> list[SelectOptionDict]:
    """Return profile select options, loading profiles if needed.

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

    if not profiles:
        return [SelectOptionDict(value="Generic Switch", label="Generic Switch")]

    return [SelectOptionDict(value=p.name, label=p.name) for p in profiles]


class TuyaCloudlessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Tuya Cloudless config flow.

    Provides guided setup with automatic device discovery and a manual
    fallback for users who have device details available.
    """

    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered: list[dict[str, Any]] = []
        self._device: dict[str, Any] = {}

    # ── Step 0: Pairing tool deep-link ─────────────────────────────────────────

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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

            if gw_id and len(local_key) == _LOCAL_KEY_LENGTH and ip_address:
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

    # ── Step 1: Search ─────────────────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: Present setup options and optionally scan the network.

        Offers two paths: automatic network scan or manual device entry.
        When *Search* is selected, the integration listens for device
        announcements on the local network for a few seconds.

        Args:
            user_input: Submitted form data, or ``None`` on first render.

        Returns:
            Config flow result directing to the next step.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("setup_mode") == "manual":
                return await self.async_step_manual()

            # Run UDP discovery
            try:
                self._discovered = await asyncio.wait_for(
                    self._run_discovery(),
                    timeout=_DISCOVERY_TIMEOUT,
                )
            except TimeoutError:
                self._discovered = []
            except OSError as exc:
                _LOGGER.debug("Discovery socket error: %s", exc)
                self._discovered = []

            if self._discovered:
                return await self.async_step_select()

            errors["base"] = "no_devices_found"

        schema = vol.Schema(
            {
                vol.Required("setup_mode", default="search"): vol.In(["search", "manual"]),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

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
        default_profile = profile_options[0]["value"] if profile_options else "Generic Switch"

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

            errors = await self._check_connection(ip_address)

            if not errors:
                await self.async_set_unique_id(gw_id)
                self._abort_if_unique_id_configured()
                title = self._device.get(CONF_DEVICE_NAME) or ip_address
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_GW_ID: gw_id,
                        CONF_LOCAL_KEY: self._device[CONF_LOCAL_KEY],
                        CONF_IP_ADDRESS: ip_address,
                        CONF_PROTOCOL_VERSION: self._device.get(
                            CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
                        ),
                        CONF_PROFILE: self._device.get(CONF_PROFILE, "Generic Switch"),
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

            if not gw_id:
                errors[CONF_GW_ID] = "invalid_gw_id"
            if len(local_key) != _LOCAL_KEY_LENGTH:
                errors[CONF_LOCAL_KEY] = "invalid_local_key"
            if not ip_address:
                errors[CONF_IP_ADDRESS] = "cannot_connect"

            if not errors:
                errors = await self._check_connection(ip_address)

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
        default_profile = profile_options[0]["value"] if profile_options else "Generic Switch"

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
        default_profile = profile_options[0]["value"] if profile_options else "Generic Switch"

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
            else:
                ip_address = user_input.get(CONF_IP_ADDRESS, "").strip() or reauth_entry.data.get(
                    CONF_IP_ADDRESS, ""
                )
                errors = await self._check_connection(ip_address)

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
            elif not ip_address:
                errors[CONF_IP_ADDRESS] = "invalid_ip"
            else:
                errors = await self._check_connection(ip_address)

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
                vol.Required(
                    CONF_LOCAL_KEY,
                    default=reconfigure_entry.data.get(CONF_LOCAL_KEY, ""),
                ): str,
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

    def _pairing_tool_url(self) -> str:
        """Return the URL to the Tuya Cloudless pairing tool running on this HA host.

        Extracts the hostname from HA's internal URL and appends port 8099.
        Falls back to ``http://homeassistant.local:8099`` when the internal URL
        is not configured.

        Returns:
            Absolute HTTP URL string for the pairing tool.
        """
        try:
            internal = getattr(self.hass.config, "internal_url", None)
        except AttributeError:
            internal = None
        if isinstance(internal, str) and internal:
            parsed = urlparse(internal)
            host = parsed.hostname or "homeassistant.local"
            return f"http://{host}:8099"
        return "http://homeassistant.local:8099"

    # ── Private helpers ────────────────────────────────────────────────────────

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

    async def _check_connection(self, ip_address: str) -> dict[str, str]:
        """Try a TCP connect to confirm the device is reachable.

        Args:
            ip_address: Device IP address.

        Returns:
            Error dict mapping field name to error key.
            Empty if the connection succeeded.
        """
        errors: dict[str, str] = {}
        try:
            _reader, _writer = await asyncio.wait_for(
                asyncio.open_connection(ip_address, DEFAULT_TCP_PORT),
                timeout=_CONNECTION_TIMEOUT,
            )
            _writer.close()
            with contextlib.suppress(OSError):
                await _writer.wait_closed()
        except (TimeoutError, OSError) as exc:
            _LOGGER.debug("Connection test to %s failed: %s", ip_address, exc)
            errors[CONF_IP_ADDRESS] = "cannot_connect"
        return errors


# ── Options flow ───────────────────────────────────────────────────────────────


class TuyaCloudlessOptionsFlow(OptionsFlow):
    """Handle Tuya Cloudless options (IP address, device generation)."""

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
                    CONF_IP_ADDRESS,
                    default=self.config_entry.data.get(CONF_IP_ADDRESS, ""),
                ): str,
                vol.Optional(
                    CONF_PROTOCOL_VERSION,
                    default=self.config_entry.data.get(
                        CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
                    ),
                ): vol.In(PROTOCOL_VERSIONS),
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
