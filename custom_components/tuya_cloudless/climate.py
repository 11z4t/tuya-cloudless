"""Climate platform for Tuya Cloudless."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from tuya_cloudless.profiles import EntitySpec

from .coordinator import TuyaCloudlessCoordinator
from .entity import RestoreStateMixin, TuyaCloudlessEntity

_LOGGER = logging.getLogger(__name__)

# Protect single-threaded Tuya devices from concurrent HA service calls
PARALLEL_UPDATES = 1

# Map Tuya mode strings to HA HVACMode
_TUYA_TO_HA_MODE: dict[str, HVACMode] = {
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "auto": HVACMode.AUTO,
    "fan_only": HVACMode.FAN_ONLY,
    "dry": HVACMode.DRY,
    "manual": HVACMode.HEAT,
    "eco": HVACMode.HEAT,
    "off": HVACMode.OFF,
}

# Reverse map (primary Tuya string for each HA mode)
_HA_TO_TUYA_MODE: dict[HVACMode, str] = {
    HVACMode.HEAT: "heat",
    HVACMode.COOL: "cool",
    HVACMode.AUTO: "auto",
    HVACMode.FAN_ONLY: "fan_only",
    HVACMode.DRY: "dry",
    HVACMode.OFF: "off",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya Cloudless climate entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "climate"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessClimate(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessClimate(RestoreStateMixin, TuyaCloudlessEntity, ClimateEntity):
    """Tuya Cloudless climate entity (heater, AC, thermostat).

    Maps power, mode, and temperature DPs to the HA climate interface.
    The temperature scale factor is stored in ``dp_temp_set.scale``:
    temperature = raw * scale, raw = round(temperature / scale).
    This is the same convention used by sensor and number entities.
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the climate entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_power.id if spec.dp_power else "1"
        super().__init__(coordinator, dp_id=dp_id, spec=spec)

        # Build HVAC modes list — always start with OFF
        ha_modes: list[HVACMode] = [HVACMode.OFF]
        for tuya_mode in spec.dp_options:
            ha_mode = _TUYA_TO_HA_MODE.get(tuya_mode)
            if ha_mode is not None and ha_mode != HVACMode.OFF and ha_mode not in ha_modes:
                ha_modes.append(ha_mode)
        self._attr_hvac_modes = ha_modes
        self._tuya_options = list(spec.dp_options)

        # Temperature range (display = raw * scale)
        dp_temp = spec.dp_temp_set
        if spec.target_min is not None:
            self._attr_min_temp = spec.target_min
        elif dp_temp is not None and dp_temp.min_raw is not None:
            self._attr_min_temp = float(dp_temp.min_raw) * dp_temp.scale
        else:
            self._attr_min_temp = 7.0

        if spec.target_max is not None:
            self._attr_max_temp = spec.target_max
        elif dp_temp is not None and dp_temp.max_raw is not None:
            self._attr_max_temp = float(dp_temp.max_raw) * dp_temp.scale
        else:
            self._attr_max_temp = 35.0

        self._attr_target_temperature_step = spec.step

        # Optimistic state — set before command, cleared by coordinator update.
        self._optimistic_hvac_mode: HVACMode | None = None
        self._optimistic_target_temp: float | None = None

        # Supported features
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if spec.dp_temp_set is not None:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        """Register with HA and restore last known HVAC mode if available.

        Restores ``_optimistic_hvac_mode`` so the entity shows its previous
        mode immediately after an HA restart, before the device sends its
        first state push.
        """
        await super().async_added_to_hass()
        if self._restored_state is not None:
            try:
                restored_mode = HVACMode(self._restored_state)
                if restored_mode in self._attr_hvac_modes:
                    self._optimistic_hvac_mode = restored_mode
            except ValueError:
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when the coordinator delivers live device data."""
        self._optimistic_hvac_mode = None
        self._optimistic_target_temp = None
        super()._handle_coordinator_update()

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current HVAC mode.

        Optimistic state (set before a command is confirmed) takes priority.
        Falls back to live DPs once the coordinator delivers a device update.
        """
        if self._optimistic_hvac_mode is not None:
            return self._optimistic_hvac_mode
        if self._spec.dp_power is None:
            return None
        power = self.get_dp(self._spec.dp_power.id)
        if power is None:
            return None
        if not bool(power):
            return HVACMode.OFF
        if self._spec.dp_mode is not None:
            raw_mode = self.get_dp(self._spec.dp_mode.id)
            if raw_mode is not None:
                return _TUYA_TO_HA_MODE.get(str(raw_mode), HVACMode.AUTO)
        return HVACMode.AUTO

    @property
    def current_temperature(self) -> float | None:
        """Return the current ambient temperature, or None if unavailable."""
        if self._spec.dp_temp_current is None:
            return None
        raw = self.get_dp(self._spec.dp_temp_current.id)
        if raw is None:
            return None
        return round(float(raw) * self._spec.dp_temp_current.scale, 1)

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature setpoint, or None if unavailable.

        Returns optimistic temperature when set, then falls back to live DP.
        """
        if self._spec.dp_temp_set is None:
            return None
        if self._optimistic_target_temp is not None:
            return self._optimistic_target_temp
        raw = self.get_dp(self._spec.dp_temp_set.id)
        if raw is None:
            return None
        return round(float(raw) * self._spec.dp_temp_set.scale, 1)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature with optimistic update.

        Args:
            **kwargs: HA service data; ``ATTR_TEMPERATURE`` is extracted.
        """
        if self._spec.dp_temp_set is None:
            return
        temperature = float(kwargs.get(ATTR_TEMPERATURE, 20.0))
        scale = self._spec.dp_temp_set.scale
        raw_value = round(temperature / scale)
        self._optimistic_target_temp = round(float(raw_value) * scale, 1)
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_dps({self._spec.dp_temp_set.id: raw_value})
        except HomeAssistantError:
            self._optimistic_target_temp = None
            self.async_write_ha_state()
            raise

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode with optimistic update.

        Args:
            hvac_mode: Target HA HVAC mode.
        """
        if self._spec.dp_power is None:
            return
        self._optimistic_hvac_mode = hvac_mode
        self.async_write_ha_state()
        try:
            if hvac_mode == HVACMode.OFF:
                await self.coordinator.async_send_dps({self._spec.dp_power.id: False})
            else:
                dps: dict[str, Any] = {self._spec.dp_power.id: True}
                if self._spec.dp_mode is not None:
                    tuya_mode = self._get_tuya_mode(hvac_mode)
                    if tuya_mode is not None:
                        dps[self._spec.dp_mode.id] = tuya_mode
                await self.coordinator.async_send_dps(dps)
        except HomeAssistantError:
            self._optimistic_hvac_mode = None
            self.async_write_ha_state()
            raise

    def _get_tuya_mode(self, ha_mode: HVACMode) -> str | None:
        """Find the Tuya mode string for the given HA mode from the profile options.

        Prefers options listed in the profile; falls back to the static map.

        Args:
            ha_mode: HA HVAC mode to convert.

        Returns:
            Tuya mode string, or ``None`` if no mapping found.
        """
        for tuya_str in self._tuya_options:
            if _TUYA_TO_HA_MODE.get(tuya_str) == ha_mode:
                return tuya_str
        return _HA_TO_TUYA_MODE.get(ha_mode)

    async def async_turn_on(self) -> None:
        """Turn the climate device on with optimistic update."""
        if self._spec.dp_power is None:
            return
        self._optimistic_hvac_mode = HVACMode.AUTO
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_dps({self._spec.dp_power.id: True})
        except HomeAssistantError:
            self._optimistic_hvac_mode = None
            self.async_write_ha_state()
            raise

    async def async_turn_off(self) -> None:
        """Turn the climate device off with optimistic update."""
        if self._spec.dp_power is None:
            return
        self._optimistic_hvac_mode = HVACMode.OFF
        self.async_write_ha_state()
        try:
            await self.coordinator.async_send_dps({self._spec.dp_power.id: False})
        except HomeAssistantError:
            self._optimistic_hvac_mode = None
            self.async_write_ha_state()
            raise

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return raw DP values for all climate data points (for diagnostics)."""
        attrs: dict[str, Any] = {}
        if self._spec.dp_power is not None:
            attrs["dp_power_raw"] = self.get_dp(self._spec.dp_power.id)
        if self._spec.dp_mode is not None:
            attrs["dp_mode_raw"] = self.get_dp(self._spec.dp_mode.id)
        if self._spec.dp_temp_set is not None:
            attrs["dp_temp_set_raw"] = self.get_dp(self._spec.dp_temp_set.id)
        if self._spec.dp_temp_current is not None:
            attrs["dp_temp_current_raw"] = self.get_dp(self._spec.dp_temp_current.id)
        return attrs
