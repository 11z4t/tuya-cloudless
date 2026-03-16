"""Constants for the Tuya Cloudless Home Assistant integration."""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CONF_DEVICE_NAME",
    "CONF_DEVICE_TYPE",
    "CONF_GW_ID",
    "CONF_IP_ADDRESS",
    "CONF_LOCAL_KEY",
    "CONF_PROFILE",
    "CONF_PROTOCOL_VERSION",
    "DEVICE_TYPES",
    "DEVICE_TYPE_DIMMER",
    "DEVICE_TYPE_GENERIC",
    "DEVICE_TYPE_LIGHT",
    "DEVICE_TYPE_PLUG",
    "DEVICE_TYPE_SWITCH",
    "DEVICE_TYPE_TO_PROFILE",
    "DOMAIN",
    "EVENT_TUYA_CONNECTED",
    "EVENT_TUYA_DISCONNECTED",
    "EVENT_TUYA_DP_CHANGED",
    "PLATFORMS",
    "PROFILES_DIR",
]

DOMAIN = "tuya_cloudless"

# Supported HA platforms
PLATFORMS: list[str] = [
    "switch",
    "light",
    "sensor",
    "binary_sensor",
    "cover",
    "number",
    "select",
    "button",
    "climate",
    "fan",
]

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
# Bundled path: custom_components/tuya_cloudless/profiles/ (HACS install)
# Dev fallback: repo-root/profiles/ (local development)
_BUNDLED_PROFILES: Path = Path(__file__).parent / "profiles"
_DEV_PROFILES: Path = Path(__file__).parent.parent.parent / "profiles"
PROFILES_DIR: Path = _BUNDLED_PROFILES if _BUNDLED_PROFILES.is_dir() else _DEV_PROFILES

# Connection
DEFAULT_TCP_PORT = 6668
DEFAULT_COMMAND_TIMEOUT = 5.0
HEARTBEAT_INTERVAL = 20.0

# Options keys (stored in entry.options, override defaults above)
CONF_OPT_HEARTBEAT_INTERVAL = "heartbeat_interval"
CONF_OPT_COMMAND_TIMEOUT = "command_timeout"
CONF_OPT_RECONNECT_MAX_DELAY = "reconnect_max_delay"

# Options defaults
DEFAULT_OPT_HEARTBEAT_INTERVAL = 20
DEFAULT_OPT_COMMAND_TIMEOUT = 5
DEFAULT_OPT_RECONNECT_MAX_DELAY = 300

# Config entry version
# v1: initial
# v2: ip_address and protocol_version moved exclusively to entry.data;
#     options flow only exposes timing/behaviour parameters (PLAT-715).
CONFIG_ENTRY_VERSION = 2

# Number of consecutive connection failures before raising a connectivity repair issue
CONNECTIVITY_ISSUE_THRESHOLD: int = 10

# Logbook / device trigger event types
EVENT_TUYA_CONNECTED = "tuya_cloudless_connected"
EVENT_TUYA_DISCONNECTED = "tuya_cloudless_disconnected"
EVENT_TUYA_DP_CHANGED = "tuya_cloudless_dp_changed"
