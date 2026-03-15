"""Fan platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
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
    """Set up Tuya Cloudless fan entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "fan"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessFan(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessFan(TuyaCloudlessEntity, FanEntity):
    """Tuya Cloudless fan entity.

    Supports on/off, speed percentage, preset modes, oscillation, and direction.
    Only features with corresponding DPs defined in the profile are advertised.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the fan entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_power.id if spec.dp_power else "1"
        super().__init__(coordinator, dp_id=dp_id)
        self._spec = spec
        self._attr_unique_id = f"{coordinator._gw_id}_{spec.platform}_{spec.name}"
        self._attr_translation_key = spec.name

        # Preset modes from dp_options (only if dp_mode is also defined)
        if spec.dp_mode is not None and spec.dp_options:
            self._attr_preset_modes = list(spec.dp_options)
        else:
            self._attr_preset_modes = None

        # Only advertise features that have corresponding DPs
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if spec.dp_value is not None:
            features |= FanEntityFeature.SET_SPEED
        if spec.dp_mode is not None and spec.dp_options:
            features |= FanEntityFeature.PRESET_MODE
        if spec.dp_oscillate is not None:
            features |= FanEntityFeature.OSCILLATE
        if spec.dp_direction is not None:
            features |= FanEntityFeature.DIRECTION
        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        """Return True if the fan is running."""
        if self._spec.dp_power is None:
            return None
        value = self.get_dp(self._spec.dp_power.id)
        if value is None:
            return None
        return bool(value)

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage (raw DP value), or None."""
        if self._spec.dp_value is None:
            return None
        value = self.get_dp(self._spec.dp_value.id)
        if value is None:
            return None
        return int(value)

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset mode string, or None."""
        if self._spec.dp_mode is None:
            return None
        value = self.get_dp(self._spec.dp_mode.id)
        if value is None:
            return None
        return str(value)

    @property
    def oscillating(self) -> bool | None:
        """Return True if the fan is oscillating, or None."""
        if self._spec.dp_oscillate is None:
            return None
        value = self.get_dp(self._spec.dp_oscillate.id)
        if value is None:
            return None
        return bool(value)

    @property
    def current_direction(self) -> str | None:
        """Return the current fan direction string, or None."""
        if self._spec.dp_direction is None:
            return None
        value = self.get_dp(self._spec.dp_direction.id)
        if value is None:
            return None
        return str(value)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally setting speed or preset mode.

        Args:
            percentage: Optional speed percentage to set on turn-on.
            preset_mode: Optional preset mode string to set on turn-on.
            **kwargs: Additional HA service attributes (ignored).
        """
        if self._spec.dp_power is None:
            return
        dps: dict[str, Any] = {self._spec.dp_power.id: True}
        if percentage is not None and self._spec.dp_value is not None:
            dps[self._spec.dp_value.id] = percentage
        if preset_mode is not None and self._spec.dp_mode is not None:
            dps[self._spec.dp_mode.id] = preset_mode
        await self.coordinator.async_send_dps(dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        if self._spec.dp_power is None:
            return
        await self.coordinator.async_send_dps({self._spec.dp_power.id: False})

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed percentage.

        Args:
            percentage: Speed value to send to the device DP.
        """
        if self._spec.dp_value is None:
            return
        await self.coordinator.async_send_dps({self._spec.dp_value.id: percentage})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the fan preset mode.

        Args:
            preset_mode: Preset mode string to send to the device DP.
        """
        if self._spec.dp_mode is None:
            return
        await self.coordinator.async_send_dps({self._spec.dp_mode.id: preset_mode})

    async def async_oscillate(self, oscillating: bool) -> None:
        """Control fan oscillation.

        Args:
            oscillating: True to enable, False to disable oscillation.
        """
        if self._spec.dp_oscillate is None:
            return
        await self.coordinator.async_send_dps({self._spec.dp_oscillate.id: oscillating})

    async def async_set_direction(self, direction: str) -> None:
        """Set the fan rotation direction.

        Args:
            direction: Direction string (e.g. "forward" or "reverse").
        """
        if self._spec.dp_direction is None:
            return
        await self.coordinator.async_send_dps({self._spec.dp_direction.id: direction})
