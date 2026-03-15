"""Tests for custom_components.tuya_cloudless.const."""

from __future__ import annotations

from pathlib import Path

from custom_components.tuya_cloudless.const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_GW_ID,
    CONF_IP_ADDRESS,
    CONF_LOCAL_KEY,
    CONF_OPT_COMMAND_TIMEOUT,
    CONF_OPT_HEARTBEAT_INTERVAL,
    CONF_OPT_RECONNECT_MAX_DELAY,
    CONF_PROFILE,
    CONF_PROTOCOL_VERSION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_OPT_COMMAND_TIMEOUT,
    DEFAULT_OPT_HEARTBEAT_INTERVAL,
    DEFAULT_OPT_RECONNECT_MAX_DELAY,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_TCP_PORT,
    DEVICE_TYPE_TO_PROFILE,
    DEVICE_TYPES,
    DOMAIN,
    HEARTBEAT_INTERVAL,
    PLATFORMS,
    PROFILES_DIR,
    PROTOCOL_VERSIONS,
)


class TestConstants:
    def test_domain(self) -> None:
        assert DOMAIN == "tuya_cloudless"

    def test_platforms_includes_all(self) -> None:
        assert "switch" in PLATFORMS
        assert "light" in PLATFORMS
        assert "sensor" in PLATFORMS
        assert "binary_sensor" in PLATFORMS
        assert "cover" in PLATFORMS

    def test_device_types(self) -> None:
        assert "generic" in DEVICE_TYPES
        assert "switch" in DEVICE_TYPES
        assert "light" in DEVICE_TYPES
        assert "dimmer" in DEVICE_TYPES
        assert "plug" in DEVICE_TYPES

    def test_protocol_versions(self) -> None:
        assert "3.1" in PROTOCOL_VERSIONS
        assert "3.3" in PROTOCOL_VERSIONS
        assert "3.5" in PROTOCOL_VERSIONS
        assert DEFAULT_PROTOCOL_VERSION == "3.3"

    def test_tcp_port(self) -> None:
        assert DEFAULT_TCP_PORT == 6668

    def test_timing_defaults(self) -> None:
        assert HEARTBEAT_INTERVAL == 20.0
        assert DEFAULT_COMMAND_TIMEOUT == 5.0

    def test_options_defaults(self) -> None:
        assert DEFAULT_OPT_HEARTBEAT_INTERVAL == 20
        assert DEFAULT_OPT_COMMAND_TIMEOUT == 5
        assert DEFAULT_OPT_RECONNECT_MAX_DELAY == 300

    def test_config_entry_version(self) -> None:
        assert CONFIG_ENTRY_VERSION == 1

    def test_conf_keys_are_strings(self) -> None:
        for key in (
            CONF_GW_ID,
            CONF_LOCAL_KEY,
            CONF_IP_ADDRESS,
            CONF_PROTOCOL_VERSION,
            CONF_DEVICE_TYPE,
            CONF_DEVICE_NAME,
            CONF_PROFILE,
            CONF_OPT_HEARTBEAT_INTERVAL,
            CONF_OPT_COMMAND_TIMEOUT,
            CONF_OPT_RECONNECT_MAX_DELAY,
        ):
            assert isinstance(key, str)

    def test_device_type_to_profile_mapping(self) -> None:
        assert DEVICE_TYPE_TO_PROFILE["switch"] == "Generic Switch"
        assert DEVICE_TYPE_TO_PROFILE["light"] == "Generic Light"
        assert DEVICE_TYPE_TO_PROFILE["dimmer"] == "Generic Light"
        assert DEVICE_TYPE_TO_PROFILE["plug"] == "Smart Plug"
        assert DEVICE_TYPE_TO_PROFILE["generic"] == "Generic Switch"

    def test_profiles_dir_is_path(self) -> None:
        assert isinstance(PROFILES_DIR, Path)

    def test_conf_profile_key(self) -> None:
        assert CONF_PROFILE == "profile"

    def test_options_keys(self) -> None:
        assert CONF_OPT_HEARTBEAT_INTERVAL == "heartbeat_interval"
        assert CONF_OPT_COMMAND_TIMEOUT == "command_timeout"
        assert CONF_OPT_RECONNECT_MAX_DELAY == "reconnect_max_delay"
