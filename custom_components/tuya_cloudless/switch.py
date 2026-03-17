"""Switch platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from tuya_cloudless.profiles import EntitySpec

from .coordinator import TuyaCloudlessCoordinator
from .entity import RestoreStateMixin, TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

# Protect single-threaded Tuya devices from concurrent HA service calls
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless switch entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "switch"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessSwitch(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessSwitch(RestoreStateMixin, TuyaCloudlessEntity, SwitchEntity):
    """Tuya Cloudless on/off switch entity.

    Maps a boolean DP to the HA switch state. The DP identifier and entity
    name are driven by the device profile rather than hardcoded values.

    Supports optimistic state updates (state changes appear immediately in the
    UI before the device confirms) and RestoreEntity (last known state is
    restored on HA restart so the entity is never stuck as "unavailable").
    """

    _attr_assumed_state: bool = False

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the switch entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_power.id if spec.dp_power else "1"
        super().__init__(coordinator, dp_id=dp_id, spec=spec)
        self._optimistic_state: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Register with HA and restore last known switch state if available.

        Restores ``_optimistic_state`` from the last recorded HA state so that
        the switch shows its previous on/off value immediately after an HA
        restart, before the device sends its first state push.
        """
        await super().async_added_to_hass()
        if self._restored_state in (STATE_ON, STATE_OFF):
            self._optimistic_state = self._restored_state == STATE_ON

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on.

        Prefers the live DP value from the device when available; falls back
        to the optimistic / restored state while waiting for the first push.
        """
        dp_val = self.get_dp()
        if dp_val is not None:
            return bool(dp_val)
        return self._optimistic_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch with optimistic state update.

        Sets the optimistic state immediately so the UI reflects the change
        without waiting for device confirmation.  If the send fails the
        optimistic state is reverted and the exception is re-raised.
        """
        self._optimistic_state = True
        self.async_write_ha_state()
        try:
            dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
            await self.async_send_dp(dp_id, True)
        except HomeAssistantError:
            self._optimistic_state = None
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch with optimistic state update.

        Sets the optimistic state immediately so the UI reflects the change
        without waiting for device confirmation.  If the send fails the
        optimistic state is reverted and the exception is re-raised.
        """
        self._optimistic_state = False
        self.async_write_ha_state()
        try:
            dp_id = self._spec.dp_power.id if self._spec.dp_power else "1"
            await self.async_send_dp(dp_id, False)
        except HomeAssistantError:
            self._optimistic_state = None
            self.async_write_ha_state()
            raise
