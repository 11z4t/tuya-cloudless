"""Select platform for Tuya Cloudless."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
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
    """Set up Tuya Cloudless select entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "select"]
    if not specs:
        return

    valid_specs = []
    for spec in specs:
        if not spec.dp_options:
            # R44-F3: HA SelectEntity requires at least one option; an empty
            # list causes a state-write validation error in HA core.
            _LOGGER.warning(
                "[%s] Select entity '%s' has no dp_options in profile — skipping",
                runtime.coordinator.gw_id,
                spec.name,
            )
            continue
        valid_specs.append(spec)

    if valid_specs:
        async_add_entities([TuyaCloudlessSelect(runtime.coordinator, spec) for spec in valid_specs])


class TuyaCloudlessSelect(TuyaCloudlessEntity, SelectEntity):
    """Tuya Cloudless enum select entity (e.g. mode selector, alarm sound).

    Maps an enum DP to the HA select entity. The allowed options are read
    from ``spec.dp_options`` and the current DP value is validated against
    them before being returned.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the select entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_value.id if spec.dp_value else "1"
        super().__init__(coordinator, dp_id=dp_id, spec=spec)
        self._attr_options = list(spec.dp_options)

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option, or None if unknown/invalid."""
        if self._spec.dp_value is None:
            return None
        value = self.get_dp(self._spec.dp_value.id)
        if value is None:
            return None
        str_val = str(value)
        if str_val not in self._attr_options:
            return None
        return str_val

    async def async_select_option(self, option: str) -> None:
        """Select the given option by writing it to the device DP.

        Args:
            option: One of the strings in ``options``.
        """
        if self._spec.dp_value is None:
            return
        if option not in self._attr_options:
            _LOGGER.warning(
                "[%s] Option '%s' not in allowed list — ignored",
                self.coordinator.gw_id,
                option,
            )
            return
        # R44-F7: No optimistic state to revert — propagate HomeAssistantError naturally.
        await self.coordinator.async_send_dps({self._spec.dp_value.id: option})
