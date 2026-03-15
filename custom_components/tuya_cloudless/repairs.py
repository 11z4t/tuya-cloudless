"""Repair issue flows for Tuya Cloudless.

Provides a guided fix for authentication failures — directs the user to
the re-authentication flow so they can enter a new security key without
removing and re-adding the integration.
"""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


class TuyaCloudlessAuthRepairFlow(ConfirmRepairFlow):
    """Repair flow for authentication failures.

    Confirms the issue and triggers re-authentication via the config entry
    re-auth flow, where the user can enter the new security key.
    """


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Return the repair flow for the given issue.

    Args:
        hass: Home Assistant instance.
        issue_id: The issue identifier string.
        data: Optional extra data attached to the issue.

    Returns:
        A :class:`RepairsFlow` instance that handles the user interaction.
    """
    return TuyaCloudlessAuthRepairFlow()
