"""Tests for custom_components.tuya_cloudless.logbook."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.tuya_cloudless.const import (
    CONF_GW_ID,
    DOMAIN,
    EVENT_TUYA_CONNECTED,
    EVENT_TUYA_DISCONNECTED,
    EVENT_TUYA_DP_CHANGED,
)
from custom_components.tuya_cloudless.logbook import async_describe_events


def _capture_describers() -> tuple[dict, object]:
    """Call async_describe_events and collect registered describers."""
    describer: dict = {}

    def capture(domain: str, event_type: str, fn) -> None:
        describer[event_type] = (domain, fn)

    async_describe_events(MagicMock(), capture)
    return describer


def _make_event(data: dict) -> MagicMock:
    event = MagicMock()
    event.data = data
    return event


class TestLogbookRegistration:
    def test_all_three_events_registered(self) -> None:
        describer = _capture_describers()
        assert EVENT_TUYA_CONNECTED in describer
        assert EVENT_TUYA_DISCONNECTED in describer
        assert EVENT_TUYA_DP_CHANGED in describer

    def test_events_registered_under_correct_domain(self) -> None:
        describer = _capture_describers()
        for event_type, (domain, _) in describer.items():
            assert domain == DOMAIN, f"{event_type} registered under wrong domain"


class TestConnectedEvent:
    def test_name_is_tuya_cloudless(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_CONNECTED]
        result = fn(_make_event({CONF_GW_ID: "abc123"}))
        assert result["name"] == "Tuya Cloudless"

    def test_message_contains_gw_id(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_CONNECTED]
        result = fn(_make_event({CONF_GW_ID: "abc123"}))
        assert "abc123" in result["message"]

    def test_message_indicates_connected(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_CONNECTED]
        result = fn(_make_event({CONF_GW_ID: "gw001"}))
        assert "connected" in result["message"].lower()

    def test_message_falls_back_when_no_gw_id(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_CONNECTED]
        result = fn(_make_event({}))
        assert "name" in result
        assert "message" in result


class TestDisconnectedEvent:
    def test_name_is_tuya_cloudless(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DISCONNECTED]
        result = fn(_make_event({CONF_GW_ID: "abc123"}))
        assert result["name"] == "Tuya Cloudless"

    def test_message_contains_gw_id(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DISCONNECTED]
        result = fn(_make_event({CONF_GW_ID: "abc123"}))
        assert "abc123" in result["message"]

    def test_message_indicates_disconnected(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DISCONNECTED]
        result = fn(_make_event({CONF_GW_ID: "gw001"}))
        assert "disconnected" in result["message"].lower()

    def test_message_falls_back_when_no_gw_id(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DISCONNECTED]
        result = fn(_make_event({}))
        assert "name" in result
        assert "message" in result


class TestDpChangedEvent:
    def test_name_is_tuya_cloudless(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DP_CHANGED]
        result = fn(_make_event({CONF_GW_ID: "abc123", "dps": {"1": True}}))
        assert result["name"] == "Tuya Cloudless"

    def test_message_contains_gw_id(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DP_CHANGED]
        result = fn(_make_event({CONF_GW_ID: "abc123", "dps": {"1": True}}))
        assert "abc123" in result["message"]

    def test_message_includes_dp_keys(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DP_CHANGED]
        result = fn(_make_event({CONF_GW_ID: "gw001", "dps": {"1": True, "2": 50}}))
        assert "1" in result["message"]
        assert "2" in result["message"]

    def test_message_handles_empty_dps(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DP_CHANGED]
        result = fn(_make_event({CONF_GW_ID: "gw001", "dps": {}}))
        assert "name" in result
        assert "message" in result

    def test_message_handles_missing_dps(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DP_CHANGED]
        result = fn(_make_event({CONF_GW_ID: "gw001"}))
        assert "name" in result
        assert "message" in result

    def test_dp_keys_sorted(self) -> None:
        describer = _capture_describers()
        _, fn = describer[EVENT_TUYA_DP_CHANGED]
        result = fn(_make_event({CONF_GW_ID: "gw001", "dps": {"20": 100, "1": True}}))
        msg = result["message"]
        # "1" should appear before "20" in sorted order
        assert msg.index("1") < msg.index("20")
