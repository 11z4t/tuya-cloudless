"""Number platform for Tuya Cloudless."""

from __future__ import annotations

import logging
import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
    """Set up Tuya Cloudless number entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "number"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessNumber(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessNumber(TuyaCloudlessEntity, NumberEntity):
    """Tuya Cloudless numeric value entity (e.g. volume, timer, temperature offset).

    Maps a numeric DP to the HA number entity. The raw DP value is multiplied
    by ``spec.dp_value.scale`` to produce the display value, and divided when
    writing back to the device.
    """

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the number entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_value.id if spec.dp_value else "1"
        super().__init__(coordinator, dp_id=dp_id, spec=spec)

        dp = spec.dp_value
        # R42-F3: apply scale factor so HA displays the correct display range,
        # not the raw DP range.  target_min/max override as explicit display bounds.
        self._attr_native_min_value = (
            spec.target_min
            if spec.target_min is not None
            else (
                float(dp.min_raw) * dp.scale if dp is not None and dp.min_raw is not None else 0.0
            )
        )
        self._attr_native_max_value = (
            spec.target_max
            if spec.target_max is not None
            else (
                float(dp.max_raw) * dp.scale if dp is not None and dp.max_raw is not None else 100.0
            )
        )
        self._attr_native_step = spec.step

        if spec.unit:
            self._attr_native_unit_of_measurement = spec.unit

        if spec.device_class:
            from homeassistant.components.number import NumberDeviceClass

            try:
                self._attr_device_class = NumberDeviceClass(spec.device_class)
            except ValueError:
                _LOGGER.warning(
                    "[%s] Unknown device_class '%s' for number '%s' — ignored",
                    coordinator.gw_id,
                    spec.device_class,
                    spec.name,
                )

    @property
    def native_value(self) -> float | None:
        """Return the current value scaled by the DP spec's scale factor."""
        if self._spec.dp_value is None:
            return None
        raw = self.get_dp(self._spec.dp_value.id)
        if raw is None:
            return None
        scale = self._spec.dp_value.scale
        try:
            value = float(raw) * scale
        except (ValueError, TypeError):
            return None
        return max(self._attr_native_min_value, min(self._attr_native_max_value, value))

    async def async_set_native_value(self, value: float) -> None:
        """Set the number value, converting display value back to raw DP.

        Args:
            value: New display value (will be divided by scale before sending).
        """
        if self._spec.dp_value is None:
            return
        dp_id = self._spec.dp_value.id
        scale = self._spec.dp_value.scale
        if scale == 0.0:
            _LOGGER.warning("[%s] DP scale is 0 — cannot convert value", self.coordinator.gw_id)
            return
        # R49-F7: Guard against OverflowError when scale is extremely small (e.g.
        # 1e-300 from a malformed profile). round(value / scale) would raise
        # OverflowError which is not caught below (only HomeAssistantError is).
        result = value / scale
        if not math.isfinite(result):
            _LOGGER.warning(
                "[%s] DP value overflow (value=%s scale=%s) — ignoring set_native_value",
                self.coordinator.gw_id,
                value,
                scale,
            )
            return
        raw_value = round(result)
        try:
            await self.coordinator.async_send_dps({dp_id: raw_value})
        except HomeAssistantError:
            raise
