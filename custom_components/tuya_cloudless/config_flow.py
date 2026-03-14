"""Config flow for Tuya Cloudless integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE_ID, CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_LOCAL_KEY, CONF_PROTOCOL_VERSION, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_LOCAL_KEY): str,
        vol.Optional(CONF_PROTOCOL_VERSION, default="3.3"): vol.In(
            ["3.1", "3.2", "3.3", "3.4", "3.5"]
        ),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    # TODO: Implement connection test when protocol parser is ready
    # For now, just validate that required fields are present

    if not data[CONF_HOST]:
        raise InvalidHost("Host cannot be empty")

    if not data[CONF_DEVICE_ID]:
        raise InvalidDeviceID("Device ID cannot be empty")

    if not data[CONF_LOCAL_KEY] or len(data[CONF_LOCAL_KEY]) != 16:
        raise InvalidLocalKey("Local key must be 16 characters")

    # Return info that you want to store in the config entry.
    return {"title": data[CONF_NAME]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya Cloudless."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except InvalidHost:
                errors["base"] = "invalid_host"
            except InvalidDeviceID:
                errors["base"] = "invalid_device_id"
            except InvalidLocalKey:
                errors["base"] = "invalid_local_key"
            except HomeAssistantError:
                _LOGGER.exception("Unexpected config flow error")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class InvalidHost(HomeAssistantError):
    """Error to indicate invalid host."""


class InvalidDeviceID(HomeAssistantError):
    """Error to indicate invalid device ID."""


class InvalidLocalKey(HomeAssistantError):
    """Error to indicate invalid local key."""
