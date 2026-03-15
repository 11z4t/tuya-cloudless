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

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


class TuyaCloudlessAuthRepairFlow(ConfirmRepairFlow):
    """Repair flow for authentication failures.

    Confirms the issue and triggers re-authentication via the config entry
    re-auth flow, where the user can enter the new security key.
    """


class TuyaCloudlessConnectivityRepairFlow(ConfirmRepairFlow):
    """Repair flow for persistent connectivity failures.

    Confirms the issue and guides the user to the reconfigure flow to
    update the IP address or other connection settings.
    """


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Return the repair flow for the given issue.

    Args:
        hass: Home Assistant instance.
        issue_id: The issue identifier string (e.g. ``auth_failure``, ``connectivity``).
        data: Optional extra data attached to the issue.

    Returns:
        A :class:`RepairsFlow` instance appropriate for the issue type.
    """
    if issue_id == "connectivity":
        return TuyaCloudlessConnectivityRepairFlow()
    return TuyaCloudlessAuthRepairFlow()
