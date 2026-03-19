"""Repair issue flows for Tuya Cloudless.

Two repair types are provided:

auth_failure
    Raised when the device rejects the local key (e.g. after factory reset).
    Guides the user to the re-authentication flow to enter the new key.

connectivity
    Raised when the device cannot be reached on the network after multiple
    reconnect attempts.  Guides the user to check the device IP address and
    update it via the reconfigure flow if needed.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

_LOGGER = logging.getLogger(__name__)


class TuyaCloudlessAuthRepairFlow(RepairsFlow):
    """Repair flow for authentication failures.

    After the user confirms, triggers the config entry re-authentication flow
    so they can enter a new local key (e.g. after factory reset).
    """

    def __init__(self, entry_id: str) -> None:
        """Initialise the repair flow.

        Args:
            entry_id: Config entry ID for the affected device.
        """
        super().__init__()
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show the confirmation step."""
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm re-authentication and start the reauth flow.

        Args:
            user_input: Submitted form data, or None if showing the form.

        Returns:
            Form to show or completion result.
        """
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                entry.async_start_reauth(self.hass)
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


class TuyaCloudlessConnectivityRepairFlow(RepairsFlow):
    """Repair flow for persistent connectivity failures.

    Shows connectivity diagnostic info (last seen, reconnect count) and
    guides the user to the reconfigure flow to update the IP address.
    """

    def __init__(self, entry_id: str, data: dict[str, str | int | float | None] | None) -> None:
        """Initialise the repair flow.

        Args:
            entry_id: Config entry ID for the affected device.
            data: Issue data dict (may contain last_seen, reconnect_count).
        """
        super().__init__()
        self._entry_id = entry_id
        self._issue_data = data or {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show the confirmation step."""
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm the issue and offer reconfigure.

        Args:
            user_input: Submitted form data, or None if showing the form.

        Returns:
            Form to show or completion result.
        """
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None and hasattr(entry, "async_start_reconfiguration"):
                entry.async_start_reconfiguration(self.hass)
            return self.async_create_entry(data={})

        description_placeholders: dict[str, str] = {
            "last_seen": str(self._issue_data.get("last_seen", "unknown")),
            "reconnect_count": str(self._issue_data.get("reconnect_count", 0)),
        }
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=description_placeholders,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Return the repair flow for the given issue.

    Args:
        hass: Home Assistant instance.
        issue_id: The issue identifier string (e.g. ``auth_failed_<entry_id>``).
        data: Optional extra data attached to the issue.

    Returns:
        A :class:`RepairsFlow` instance appropriate for the issue type.
    """
    # Extract entry_id: issue_id format is "auth_failed_{entry_id}" or "connectivity_{entry_id}"
    # Coordinator does not pass data= to async_create_issue, so fall back to string parsing.
    entry_id = ""
    if data and "entry_id" in data:
        entry_id = str(data["entry_id"])
    elif issue_id.startswith("auth_failed_"):
        entry_id = issue_id[len("auth_failed_") :]
    elif issue_id.startswith("connectivity_"):
        entry_id = issue_id[len("connectivity_") :]

    if issue_id.startswith("connectivity"):
        return TuyaCloudlessConnectivityRepairFlow(entry_id, data)
    return TuyaCloudlessAuthRepairFlow(entry_id)
