"""Button platform for Tuya Cloudless."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from tuya_cloudless.profiles import EntitySpec

from .coordinator import TuyaCloudlessCoordinator
from .entity import TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

# Protect single-threaded Tuya devices from concurrent HA service calls
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless button entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "button"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessButton(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessButton(TuyaCloudlessEntity, ButtonEntity):
    """Tuya Cloudless one-shot button entity (e.g. reset, defrost, start cycle).

    Pressing the button sends a trigger payload to the device DP.  The DP to
    trigger and the payload are resolved in priority order:

    1. ``spec.dp_power`` — sends ``True`` (boolean trigger, most common).
    2. ``spec.dp_value`` — sends ``True`` (fallback for raw/enum trigger DPs).

    Button entities have no readable state; they are stateless by design.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the button entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        # Prefer dp_power for the canonical trigger DP; fall back to dp_value.
        dp_id = (
            spec.dp_power.id
            if spec.dp_power is not None
            else (spec.dp_value.id if spec.dp_value is not None else None)
        )
        super().__init__(coordinator, dp_id=dp_id, spec=spec)

        if spec.device_class:
            from homeassistant.components.button import ButtonDeviceClass

            try:
                self._attr_device_class = ButtonDeviceClass(spec.device_class)
            except ValueError:
                _LOGGER.warning(
                    "[%s] Unknown device_class '%s' for button '%s' — ignored",
                    coordinator.gw_id,
                    spec.device_class,
                    spec.name,
                )

    async def async_press(self) -> None:
        """Send the trigger payload to the device when the button is pressed.

        Resolves the trigger DP and sends ``True``:

        - ``dp_power`` → ``{dp_id: True}``
        - ``dp_value`` (fallback) → ``{dp_id: True}``

        If neither DP is configured the call is a no-op (profile misconfiguration).
        """
        # R44-F7: No optimistic state to revert — let HomeAssistantError
        # propagate naturally to the HA service call handler.
        if self._spec.dp_power is not None:
            await self.coordinator.async_send_dps({self._spec.dp_power.id: True})
        elif self._spec.dp_value is not None:
            await self.coordinator.async_send_dps({self._spec.dp_value.id: True})
        else:
            _LOGGER.warning(
                "[%s] Button '%s' has no trigger DP configured — press ignored",
                self.coordinator.gw_id,
                self._spec.name,
            )
