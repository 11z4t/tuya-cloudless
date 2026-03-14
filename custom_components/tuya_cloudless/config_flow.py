"""Config flow for Tuya Cloudless integration.

Supports two setup paths:
  1. Manual — user enters gwId, local key, IP, and version.
  2. UDP discovery — automatically detects devices on the LAN, user adds local key.

Both paths share the same validation logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_GW_ID,
    CONF_IP_ADDRESS,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_TCP_PORT,
    DEVICE_TYPES,
    DOMAIN,
    PROTOCOL_VERSIONS,
)

_LOGGER = logging.getLogger(__name__)

_LOCAL_KEY_LENGTH = 16


class TuyaCloudlessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Tuya Cloudless config flow."""

    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        self._discovery_info: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual device configuration step.

        Args:
            user_input: Form data submitted by the user, or None on first render.

        Returns:
            Config flow result.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            gw_id: str = user_input[CONF_GW_ID].strip()
            local_key: str = user_input[CONF_LOCAL_KEY].strip()
            ip_address: str = user_input[CONF_IP_ADDRESS].strip()

            errors = await self._validate_input(gw_id, local_key, ip_address)

            if not errors:
                await self.async_set_unique_id(gw_id)
                self._abort_if_unique_id_configured()

                title = user_input.get(CONF_DEVICE_NAME, gw_id) or gw_id
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_GW_ID: gw_id,
                        CONF_LOCAL_KEY: local_key,
                        CONF_IP_ADDRESS: ip_address,
                        CONF_PROTOCOL_VERSION: user_input.get(
                            CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
                        ),
                        CONF_DEVICE_TYPE: user_input.get(CONF_DEVICE_TYPE, "generic"),
                        CONF_DEVICE_NAME: title,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_GW_ID): str,
                vol.Required(CONF_LOCAL_KEY): str,
                vol.Required(CONF_IP_ADDRESS): str,
                vol.Optional(
                    CONF_PROTOCOL_VERSION, default=DEFAULT_PROTOCOL_VERSION
                ): vol.In(PROTOCOL_VERSIONS),
                vol.Optional(CONF_DEVICE_TYPE, default="generic"): vol.In(DEVICE_TYPES),
                vol.Optional(CONF_DEVICE_NAME, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle configuration after UDP discovery auto-detected a device.

        Args:
            user_input: Form data or None on first render.

        Returns:
            Config flow result.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            local_key = user_input[CONF_LOCAL_KEY].strip()
            gw_id = self._discovery_info[CONF_GW_ID]
            ip_address = self._discovery_info[CONF_IP_ADDRESS]

            errors = await self._validate_input(gw_id, local_key, ip_address)

            if not errors:
                await self.async_set_unique_id(gw_id)
                self._abort_if_unique_id_configured()

                title = user_input.get(CONF_DEVICE_NAME, gw_id) or gw_id
                return self.async_create_entry(
                    title=title,
                    data={
                        **self._discovery_info,
                        CONF_LOCAL_KEY: local_key,
                        CONF_DEVICE_TYPE: user_input.get(CONF_DEVICE_TYPE, "generic"),
                        CONF_DEVICE_NAME: title,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCAL_KEY): str,
                vol.Optional(CONF_DEVICE_TYPE, default="generic"): vol.In(DEVICE_TYPES),
                vol.Optional(CONF_DEVICE_NAME, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="discovery",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "ip_address": self._discovery_info.get(CONF_IP_ADDRESS, ""),
                "version": self._discovery_info.get(CONF_PROTOCOL_VERSION, ""),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TuyaCloudlessOptionsFlow:
        """Return the options flow handler."""
        return TuyaCloudlessOptionsFlow(config_entry)

    # ── Validation ─────────────────────────────────────────────────────────────

    async def _validate_input(
        self,
        gw_id: str,
        local_key: str,
        ip_address: str,
    ) -> dict[str, str]:
        """Validate user inputs; return error dict (empty = all valid).

        Args:
            gw_id: Device gateway ID string.
            local_key: Local key string.
            ip_address: IP address string.

        Returns:
            Dict of field_name → error_key. Empty if all inputs are valid.
        """
        errors: dict[str, str] = {}

        if not gw_id:
            errors[CONF_GW_ID] = "invalid_gw_id"
        if len(local_key) != _LOCAL_KEY_LENGTH:
            errors[CONF_LOCAL_KEY] = "invalid_local_key"
        if not ip_address:
            errors[CONF_IP_ADDRESS] = "cannot_connect"

        if errors:
            return errors

        # Quick connectivity test — try TCP connect on port 6668
        try:
            await asyncio.wait_for(
                asyncio.open_connection(ip_address, DEFAULT_TCP_PORT),
                timeout=3.0,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            _LOGGER.debug("Connection test to %s failed: %s", ip_address, exc)
            errors[CONF_IP_ADDRESS] = "cannot_connect"

        return errors


class TuyaCloudlessOptionsFlow(OptionsFlow):
    """Handle Tuya Cloudless options (IP address update, protocol version)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Render the options form.

        Args:
            user_input: Submitted form data or None.

        Returns:
            Config flow result.
        """
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_IP_ADDRESS,
                    default=self._config_entry.data.get(CONF_IP_ADDRESS, ""),
                ): str,
                vol.Optional(
                    CONF_PROTOCOL_VERSION,
                    default=self._config_entry.data.get(
                        CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION
                    ),
                ): vol.In(PROTOCOL_VERSIONS),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
