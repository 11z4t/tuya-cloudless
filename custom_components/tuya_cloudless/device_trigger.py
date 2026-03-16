"""Provides device triggers for Tuya Cloudless."""

from __future__ import annotations

from typing import Final

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_EVENT,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    EVENT_TUYA_CONNECTED,
    EVENT_TUYA_DISCONNECTED,
    EVENT_TUYA_DP_CHANGED,
)

TRIGGER_TYPE_CONNECTED: Final = "connected"
TRIGGER_TYPE_DISCONNECTED: Final = "disconnected"
TRIGGER_TYPE_DP_CHANGED: Final = "dp_changed"

TRIGGER_TYPES: Final = frozenset(
    {TRIGGER_TYPE_CONNECTED, TRIGGER_TYPE_DISCONNECTED, TRIGGER_TYPE_DP_CHANGED}
)

_EVENT_FOR_TYPE: Final[dict[str, str]] = {
    TRIGGER_TYPE_CONNECTED: EVENT_TUYA_CONNECTED,
    TRIGGER_TYPE_DISCONNECTED: EVENT_TUYA_DISCONNECTED,
    TRIGGER_TYPE_DP_CHANGED: EVENT_TUYA_DP_CHANGED,
}

TRIGGER_SCHEMA: Final = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List device triggers for Tuya Cloudless devices."""
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in sorted(TRIGGER_TYPES)
    ]


async def async_validate_trigger_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate trigger configuration."""
    return TRIGGER_SCHEMA(config)


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""
    trigger_type: str = config[CONF_TYPE]
    event_type: str = _EVENT_FOR_TYPE[trigger_type]

    event_config = {
        event_trigger.CONF_PLATFORM: CONF_EVENT,
        event_trigger.CONF_EVENT_TYPE: event_type,
        event_trigger.CONF_EVENT_DATA: {
            ATTR_DEVICE_ID: config[CONF_DEVICE_ID],
        },
    }

    event_config = event_trigger.TRIGGER_SCHEMA(event_config)
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
