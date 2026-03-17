"""Tests for custom_components.tuya_cloudless.device_trigger."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.tuya_cloudless.const import (
    DOMAIN,
    EVENT_TUYA_CONNECTED,
    EVENT_TUYA_DISCONNECTED,
    EVENT_TUYA_DP_CHANGED,
)
from custom_components.tuya_cloudless.device_trigger import (
    TRIGGER_TYPE_CONNECTED,
    TRIGGER_TYPE_DISCONNECTED,
    TRIGGER_TYPE_DP_CHANGED,
    TRIGGER_TYPES,
    async_attach_trigger,
    async_get_triggers,
    async_validate_trigger_config,
)

DEVICE_ID = "abc123_ha_device_registry_id"

_VALID_BASE = {
    "platform": "device",
    "domain": DOMAIN,
    "device_id": DEVICE_ID,
}


class TestTriggerTypes:
    def test_three_trigger_types_defined(self) -> None:
        assert len(TRIGGER_TYPES) == 3

    def test_expected_types_present(self) -> None:
        assert TRIGGER_TYPE_CONNECTED in TRIGGER_TYPES
        assert TRIGGER_TYPE_DISCONNECTED in TRIGGER_TYPES
        assert TRIGGER_TYPE_DP_CHANGED in TRIGGER_TYPES


class TestGetTriggers:
    @pytest.mark.asyncio
    async def test_returns_three_triggers(self) -> None:
        triggers = await async_get_triggers(MagicMock(), DEVICE_ID)
        assert len(triggers) == 3

    @pytest.mark.asyncio
    async def test_all_trigger_types_returned(self) -> None:
        triggers = await async_get_triggers(MagicMock(), DEVICE_ID)
        types = {t["type"] for t in triggers}
        assert TRIGGER_TYPE_CONNECTED in types
        assert TRIGGER_TYPE_DISCONNECTED in types
        assert TRIGGER_TYPE_DP_CHANGED in types

    @pytest.mark.asyncio
    async def test_each_trigger_has_correct_device_id(self) -> None:
        triggers = await async_get_triggers(MagicMock(), DEVICE_ID)
        for trigger in triggers:
            assert trigger["device_id"] == DEVICE_ID

    @pytest.mark.asyncio
    async def test_each_trigger_has_correct_domain(self) -> None:
        triggers = await async_get_triggers(MagicMock(), DEVICE_ID)
        for trigger in triggers:
            assert trigger["domain"] == DOMAIN

    @pytest.mark.asyncio
    async def test_each_trigger_has_platform_device(self) -> None:
        triggers = await async_get_triggers(MagicMock(), DEVICE_ID)
        for trigger in triggers:
            assert trigger["platform"] == "device"


class TestValidateTriggerConfig:
    @pytest.mark.asyncio
    async def test_connected_valid(self) -> None:
        config = {**_VALID_BASE, "type": TRIGGER_TYPE_CONNECTED}
        result = await async_validate_trigger_config(MagicMock(), config)
        assert result["type"] == TRIGGER_TYPE_CONNECTED

    @pytest.mark.asyncio
    async def test_disconnected_valid(self) -> None:
        config = {**_VALID_BASE, "type": TRIGGER_TYPE_DISCONNECTED}
        result = await async_validate_trigger_config(MagicMock(), config)
        assert result["type"] == TRIGGER_TYPE_DISCONNECTED

    @pytest.mark.asyncio
    async def test_dp_changed_valid(self) -> None:
        config = {**_VALID_BASE, "type": TRIGGER_TYPE_DP_CHANGED}
        result = await async_validate_trigger_config(MagicMock(), config)
        assert result["type"] == TRIGGER_TYPE_DP_CHANGED

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self) -> None:
        config = {**_VALID_BASE, "type": "not_a_valid_trigger"}
        with pytest.raises(vol.Invalid):
            await async_validate_trigger_config(MagicMock(), config)

    @pytest.mark.asyncio
    async def test_missing_type_raises(self) -> None:
        config = {**_VALID_BASE}
        with pytest.raises(vol.Invalid):
            await async_validate_trigger_config(MagicMock(), config)


class TestAttachTrigger:
    """Tests for async_attach_trigger — verifies correct event type and device_id filter."""

    def _make_config(self, trigger_type: str) -> dict:
        return {**_VALID_BASE, "type": trigger_type}

    def _event_type_str(self, event_config: dict) -> str:
        """Extract the event type string from the (possibly Template-wrapped) event config."""
        # event_trigger.TRIGGER_SCHEMA wraps event_type in a list of Template objects.
        raw = event_config["event_type"]
        if isinstance(raw, list):
            raw = raw[0]
        return raw.template if hasattr(raw, "template") else str(raw)

    @pytest.mark.asyncio
    async def test_connected_uses_correct_event(self) -> None:
        config = self._make_config(TRIGGER_TYPE_CONNECTED)
        with patch(
            "custom_components.tuya_cloudless.device_trigger.event_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=lambda: None,
        ) as mock_attach:
            await async_attach_trigger(MagicMock(), config, AsyncMock(), MagicMock())
        event_config = mock_attach.call_args.args[1]
        assert self._event_type_str(event_config) == EVENT_TUYA_CONNECTED

    @pytest.mark.asyncio
    async def test_disconnected_uses_correct_event(self) -> None:
        config = self._make_config(TRIGGER_TYPE_DISCONNECTED)
        with patch(
            "custom_components.tuya_cloudless.device_trigger.event_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=lambda: None,
        ) as mock_attach:
            await async_attach_trigger(MagicMock(), config, AsyncMock(), MagicMock())
        event_config = mock_attach.call_args.args[1]
        assert self._event_type_str(event_config) == EVENT_TUYA_DISCONNECTED

    @pytest.mark.asyncio
    async def test_dp_changed_uses_correct_event(self) -> None:
        config = self._make_config(TRIGGER_TYPE_DP_CHANGED)
        with patch(
            "custom_components.tuya_cloudless.device_trigger.event_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=lambda: None,
        ) as mock_attach:
            await async_attach_trigger(MagicMock(), config, AsyncMock(), MagicMock())
        event_config = mock_attach.call_args.args[1]
        assert self._event_type_str(event_config) == EVENT_TUYA_DP_CHANGED

    @pytest.mark.asyncio
    async def test_device_id_filter_applied(self) -> None:
        config = self._make_config(TRIGGER_TYPE_CONNECTED)
        with patch(
            "custom_components.tuya_cloudless.device_trigger.event_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=lambda: None,
        ) as mock_attach:
            await async_attach_trigger(MagicMock(), config, AsyncMock(), MagicMock())
        event_config = mock_attach.call_args.args[1]
        assert event_config["event_data"]["device_id"] == DEVICE_ID

    @pytest.mark.asyncio
    async def test_platform_type_is_device(self) -> None:
        config = self._make_config(TRIGGER_TYPE_DP_CHANGED)
        with patch(
            "custom_components.tuya_cloudless.device_trigger.event_trigger.async_attach_trigger",
            new_callable=AsyncMock,
            return_value=lambda: None,
        ) as mock_attach:
            await async_attach_trigger(MagicMock(), config, AsyncMock(), MagicMock())
        _, kwargs = mock_attach.call_args
        assert kwargs.get("platform_type") == "device"
