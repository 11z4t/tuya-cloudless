"""Describe Tuya Cloudless logbook events."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    CONF_GW_ID,
    DOMAIN,
    EVENT_TUYA_CONNECTED,
    EVENT_TUYA_DISCONNECTED,
    EVENT_TUYA_DP_CHANGED,
)


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict]], None],
) -> None:
    """Describe logbook events."""

    @callback
    def _describe_connected(event: Event) -> dict[str, str]:
        """Describe a tuya_cloudless_connected logbook event."""
        gw_id = event.data.get(CONF_GW_ID, "unknown")
        return {
            LOGBOOK_ENTRY_NAME: "Tuya Cloudless",
            LOGBOOK_ENTRY_MESSAGE: f"Device '{gw_id}' connected",
        }

    @callback
    def _describe_disconnected(event: Event) -> dict[str, str]:
        """Describe a tuya_cloudless_disconnected logbook event."""
        gw_id = event.data.get(CONF_GW_ID, "unknown")
        return {
            LOGBOOK_ENTRY_NAME: "Tuya Cloudless",
            LOGBOOK_ENTRY_MESSAGE: f"Device '{gw_id}' disconnected",
        }

    @callback
    def _describe_dp_changed(event: Event) -> dict[str, str]:
        """Describe a tuya_cloudless_dp_changed logbook event."""
        gw_id = event.data.get(CONF_GW_ID, "unknown")
        dps: dict = event.data.get("dps", {})
        keys = ", ".join(str(k) for k in sorted(dps.keys())) if dps else "?"
        return {
            LOGBOOK_ENTRY_NAME: "Tuya Cloudless",
            LOGBOOK_ENTRY_MESSAGE: f"Device '{gw_id}' DP update: {keys}",
        }

    async_describe_event(DOMAIN, EVENT_TUYA_CONNECTED, _describe_connected)
    async_describe_event(DOMAIN, EVENT_TUYA_DISCONNECTED, _describe_disconnected)
    async_describe_event(DOMAIN, EVENT_TUYA_DP_CHANGED, _describe_dp_changed)
