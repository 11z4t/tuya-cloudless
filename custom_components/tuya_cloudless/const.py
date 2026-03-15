"""Constants for the Tuya Cloudless Home Assistant integration."""

from __future__ import annotations

from pathlib import Path

DOMAIN = "tuya_cloudless"

# Supported HA platforms
PLATFORMS: list[str] = ["switch", "light", "sensor", "binary_sensor", "cover"]

# Config entry keys
CONF_GW_ID = "gw_id"
CONF_LOCAL_KEY = "local_key"
CONF_IP_ADDRESS = "ip_address"
CONF_PROTOCOL_VERSION = "protocol_version"
CONF_DEVICE_TYPE = "device_type"  # Legacy — replaced by CONF_PROFILE
CONF_DEVICE_NAME = "device_name"
CONF_PROFILE = "profile"  # Display name of the selected device profile

# Device types (legacy — kept for backward-compat with old config entries)
DEVICE_TYPE_SWITCH = "switch"
DEVICE_TYPE_LIGHT = "light"
DEVICE_TYPE_DIMMER = "dimmer"
DEVICE_TYPE_PLUG = "plug"
DEVICE_TYPE_GENERIC = "generic"

DEVICE_TYPES: list[str] = [
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_GENERIC,
]

# Legacy device type → profile name mapping (for backward compatibility)
DEVICE_TYPE_TO_PROFILE: dict[str, str] = {
    DEVICE_TYPE_SWITCH: "Generic Switch",
    DEVICE_TYPE_LIGHT: "Generic Light",
    DEVICE_TYPE_DIMMER: "Generic Light",
    DEVICE_TYPE_PLUG: "Smart Plug",
    DEVICE_TYPE_GENERIC: "Generic Switch",
}

# Protocol version choices (shown in UI)
PROTOCOL_VERSIONS: list[str] = ["3.1", "3.2", "3.3", "3.4", "3.5"]
DEFAULT_PROTOCOL_VERSION = "3.3"

# Device profile directory (YAML files defining entity specs per device type)
PROFILES_DIR: Path = Path(__file__).parent.parent.parent / "profiles"

# Connection
DEFAULT_TCP_PORT = 6668
DEFAULT_COMMAND_TIMEOUT = 5.0
HEARTBEAT_INTERVAL = 20.0

# Config entry version
CONFIG_ENTRY_VERSION = 1
