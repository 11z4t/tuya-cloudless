"""Fan platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
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


class TuyaCloudlessFan(RestoreStateMixin, TuyaCloudlessEntity, FanEntity):
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
        super().__init__(coordinator, dp_id=dp_id, spec=spec)

        # Preset modes from dp_options (only if dp_mode is also defined)
        if spec.dp_mode is not None and spec.dp_options:
            self._attr_preset_modes = list(spec.dp_options)
        else:
            self._attr_preset_modes = None

        # Optimistic state — set before command, cleared by coordinator update.
        self._optimistic_is_on: bool | None = None
        self._optimistic_percentage: int | None = None
        self._optimistic_preset_mode: str | None = None
        self._optimistic_oscillating: bool | None = None
        self._optimistic_direction: str | None = None

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

    async def async_added_to_hass(self) -> None:
        """Register with HA and restore last known fan on/off state if available.

        Restores ``_optimistic_is_on`` so the entity shows its previous
        state immediately after an HA restart, before the device sends its
        first state push.
        """
        await super().async_added_to_hass()
        if self._restored_state == STATE_ON:
            self._optimistic_is_on = True
        elif self._restored_state == STATE_OFF:
            self._optimistic_is_on = False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when the coordinator delivers live device data."""
        self._optimistic_is_on = None
        self._optimistic_percentage = None
        self._optimistic_preset_mode = None
        self._optimistic_oscillating = None
        self._optimistic_direction = None
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool | None:
        """Return True if the fan is running.

        Optimistic state takes priority until the coordinator delivers live data.
        """
        if self._optimistic_is_on is not None:
            return self._optimistic_is_on
        if self._spec.dp_power is None:
            return None
        value = self.get_dp(self._spec.dp_power.id)
        if value is None:
            return None
        return bool(value)

    def _raw_to_percentage(self, raw: int) -> int:
        """Map a raw DP speed value to 0-100% using dp_value min/max range."""
        dp = self._spec.dp_value
        if dp is None:
            return 0
        min_raw = dp.min_raw if dp.min_raw is not None else 0
        max_raw = dp.max_raw if dp.max_raw is not None else 100
        span = max_raw - min_raw
        if span == 0:
            return 100
        return round((raw - min_raw) / span * 100)

    def _percentage_to_raw(self, percentage: int) -> int:
        """Map a 0-100% value to a raw DP speed value using dp_value min/max range."""
        dp = self._spec.dp_value
        if dp is None:
            return percentage
        min_raw = dp.min_raw if dp.min_raw is not None else 0
        max_raw = dp.max_raw if dp.max_raw is not None else 100
        # Clamp percentage to [0, 100] as defence-in-depth (HA usually validates)
        pct = max(0, min(100, percentage))
        return min_raw + round((max_raw - min_raw) * pct / 100)

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a 0-100% value, or None if unavailable.

        Returns optimistic percentage when set, then falls back to live DP.
        """
        if self._spec.dp_value is None:
            return None
        if self._optimistic_percentage is not None:
            return self._optimistic_percentage
        value = self.get_dp(self._spec.dp_value.id)
        if value is None:
            return None
        return self._raw_to_percentage(int(value))

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset mode string, or None.

        Returns optimistic preset mode when set, then falls back to live DP.
        """
        if self._spec.dp_mode is None:
            return None
        if self._optimistic_preset_mode is not None:
            return self._optimistic_preset_mode
        value = self.get_dp(self._spec.dp_mode.id)
        if value is None:
            return None
        return str(value)

    @property
    def oscillating(self) -> bool | None:
        """Return True if the fan is oscillating, or None.

        Returns optimistic oscillating when set, then falls back to live DP.
        """
        if self._spec.dp_oscillate is None:
            return None
        if self._optimistic_oscillating is not None:
            return self._optimistic_oscillating
        value = self.get_dp(self._spec.dp_oscillate.id)
        if value is None:
            return None
        return bool(value)

    @property
    def current_direction(self) -> str | None:
        """Return the current fan direction string, or None.

        Returns optimistic direction when set, then falls back to live DP.
        """
        if self._spec.dp_direction is None:
            return None
        if self._optimistic_direction is not None:
            return self._optimistic_direction
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
        """Turn the fan on with optimistic state update.

        Args:
            percentage: Optional speed percentage to set on turn-on.
            preset_mode: Optional preset mode string to set on turn-on.
            **kwargs: Additional HA service attributes (ignored).
        """
        if self._spec.dp_power is None:
            return
        self._optimistic_is_on = True
        if percentage is not None:
            self._optimistic_percentage = percentage
        if preset_mode is not None:
            self._optimistic_preset_mode = preset_mode
        self.async_write_ha_state()
        dps: dict[str, Any] = {self._spec.dp_power.id: True}
        if percentage is not None and self._spec.dp_value is not None:
            dps[self._spec.dp_value.id] = self._percentage_to_raw(percentage)
        if preset_mode is not None and self._spec.dp_mode is not None:
            dps[self._spec.dp_mode.id] = preset_mode
        try:
            await self.coordinator.async_send_dps(dps)
        except HomeAssistantError:
            self._optimistic_is_on = None
            self._optimistic_percentage = None
            self._optimistic_preset_mode = None
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off with optimistic state update."""
        if self._spec.dp_power is None:
            return
        self._optimistic_is_on = False
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_dps({self._spec.dp_power.id: False})
        except HomeAssistantError:
            self._optimistic_is_on = None
            self.async_write_ha_state()
            raise

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed percentage (0-100) with optimistic update.

        Args:
            percentage: Speed as 0-100% — mapped to dp_value min/max raw range.
        """
        if self._spec.dp_value is None:
            return
        self._optimistic_percentage = percentage
        self.async_write_ha_state()
        raw = self._percentage_to_raw(percentage)
        try:
            await self.coordinator.async_send_dps({self._spec.dp_value.id: raw})
        except HomeAssistantError:
            self._optimistic_percentage = None
            self.async_write_ha_state()
            raise

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the fan preset mode with optimistic update.

        Args:
            preset_mode: Preset mode string to send to the device DP.
        """
        if self._spec.dp_mode is None:
            return
        self._optimistic_preset_mode = preset_mode
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_dps({self._spec.dp_mode.id: preset_mode})
        except HomeAssistantError:
            self._optimistic_preset_mode = None
            self.async_write_ha_state()
            raise

    async def async_oscillate(self, oscillating: bool) -> None:
        """Control fan oscillation with optimistic update.

        Args:
            oscillating: True to enable, False to disable oscillation.
        """
        if self._spec.dp_oscillate is None:
            return
        self._optimistic_oscillating = oscillating
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_dps({self._spec.dp_oscillate.id: oscillating})
        except HomeAssistantError:
            self._optimistic_oscillating = None
            self.async_write_ha_state()
            raise

    async def async_set_direction(self, direction: str) -> None:
        """Set the fan rotation direction with optimistic update.

        Args:
            direction: Direction string (e.g. "forward" or "reverse").
        """
        if self._spec.dp_direction is None:
            return
        self._optimistic_direction = direction
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_dps({self._spec.dp_direction.id: direction})
        except HomeAssistantError:
            self._optimistic_direction = None
            self.async_write_ha_state()
            raise

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return raw DP values for all fan data points (for diagnostics)."""
        attrs: dict[str, Any] = {}
        if self._spec.dp_power is not None:
            attrs["dp_power_raw"] = self.get_dp(self._spec.dp_power.id)
        if self._spec.dp_value is not None:
            attrs["dp_speed_raw"] = self.get_dp(self._spec.dp_value.id)
        if self._spec.dp_mode is not None:
            attrs["dp_mode_raw"] = self.get_dp(self._spec.dp_mode.id)
        if self._spec.dp_oscillate is not None:
            attrs["dp_oscillate_raw"] = self.get_dp(self._spec.dp_oscillate.id)
        if self._spec.dp_direction is not None:
            attrs["dp_direction_raw"] = self.get_dp(self._spec.dp_direction.id)
        return attrs
