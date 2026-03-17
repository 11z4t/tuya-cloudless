"""Cover platform for Tuya Cloudless.

Creates cover entities (blinds, curtains, garage doors) from device profiles.
Supports open/close, position control, tilt, and direction setting.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    STATE_CLOSED,
    STATE_OPEN,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up Tuya Cloudless cover entities from a profile.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with ``runtime_data`` attached.
        async_add_entities: Callback to register new entities.
    """
    from . import TuyaCloudlessRuntimeData

    runtime: TuyaCloudlessRuntimeData = entry.runtime_data
    specs = [s for s in runtime.entity_specs if s.platform == "cover"]
    if not specs:
        return

    async_add_entities([TuyaCloudlessCover(runtime.coordinator, spec) for spec in specs])


class TuyaCloudlessCover(RestoreStateMixin, TuyaCloudlessEntity, CoverEntity):
    """Tuya Cloudless cover entity (blinds, curtains, garage doors).

    Supports:
    - Open / close via a boolean DP.
    - Position control via an integer DP (0-100).
    - Tilt control via an optional tilt DP (0-100).
    - Optional direction DP (for motor direction control).

    ``is_closed`` is derived from position when available (position == 0),
    otherwise falls back to the boolean open DP.
    """

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        spec: EntitySpec,
    ) -> None:
        """Initialise the cover entity.

        Args:
            coordinator: The device coordinator.
            spec: Entity specification from the device profile.
        """
        dp_id = spec.dp_open.id if spec.dp_open else None
        super().__init__(coordinator, dp_id=dp_id, spec=spec)

        # Build supported features based on available DPs in spec
        features = CoverEntityFeature(0)
        if spec.dp_open is not None:
            features |= CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if spec.dp_stop is not None:
            features |= CoverEntityFeature.STOP
        if spec.dp_position is not None:
            features |= CoverEntityFeature.SET_POSITION
        if spec.dp_tilt is not None:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features

        if spec.device_class:
            with contextlib.suppress(ValueError):
                self._attr_device_class = CoverDeviceClass(spec.device_class)

        # Optimistic state — set before command, cleared by coordinator update.
        # Optimistic takes priority over live DPs until the device confirms.
        self._optimistic_open: bool | None = None
        self._optimistic_position: int | None = None
        self._optimistic_tilt: int | None = None

    async def async_added_to_hass(self) -> None:
        """Register with HA and restore last known cover state if available.

        First restores open/closed from the HA state string so the entity is
        never stuck as ``unavailable`` after a restart.  Then refines with
        the exact position and tilt values saved in the last state attributes,
        overriding the coarse open/closed guess with the real numbers.
        """
        await super().async_added_to_hass()
        # Coarse restore from state string
        if self._restored_state == STATE_OPEN:
            self._optimistic_open = True
        elif self._restored_state == STATE_CLOSED:
            self._optimistic_open = False
            if self._spec.dp_position is not None:
                self._optimistic_position = 0
        # Fine restore from saved attributes (exact position / tilt).
        # Use the cached State object from RestoreStateMixin to avoid a
        # second async_get_last_state() I/O call.
        last_state = self._restored_state_obj
        if last_state is not None:
            attrs = last_state.attributes
            raw_pos = attrs.get("current_position")
            if raw_pos is not None and self._spec.dp_position is not None:
                with contextlib.suppress(ValueError, TypeError):
                    self._optimistic_position = int(raw_pos)
                    self._optimistic_open = self._optimistic_position > 0
            raw_tilt = attrs.get("current_tilt_position")
            if raw_tilt is not None and self._spec.dp_tilt is not None:
                with contextlib.suppress(ValueError, TypeError):
                    self._optimistic_tilt = int(raw_tilt)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when the coordinator delivers live device data."""
        self._optimistic_open = None
        self._optimistic_position = None
        self._optimistic_tilt = None
        super()._handle_coordinator_update()

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is fully closed.

        Optimistic state (set before a command is confirmed) takes priority.
        Falls back to live DPs once the coordinator delivers a device update.
        """
        # Optimistic takes priority
        if self._optimistic_position is not None:
            return self._optimistic_position == 0
        if self._optimistic_open is not None:
            return not self._optimistic_open

        # Live DPs
        if self._spec.dp_position is not None:
            pos = self.current_cover_position
            if pos is None:
                return None
            return pos == 0

        if self._spec.dp_open is not None:
            value = self.get_dp(self._spec.dp_open.id)
            if value is None:
                return None
            return not bool(value)

        return None

    @property
    def current_cover_position(self) -> int | None:
        """Return current position (0 = closed, 100 = fully open), or None.

        Returns optimistic position when set (before device confirms), then
        falls back to the live DP value.
        """
        if self._spec.dp_position is None:
            return None
        if self._optimistic_position is not None:
            return self._optimistic_position
        raw = self.get_dp(self._spec.dp_position.id)
        if raw is None:
            return None
        return int(raw)

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return current tilt position (0 = closed, 100 = open), or None.

        Returns optimistic tilt when set, then falls back to the live DP.
        """
        if self._spec.dp_tilt is None:
            return None
        if self._optimistic_tilt is not None:
            return self._optimistic_tilt
        raw = self.get_dp(self._spec.dp_tilt.id)
        if raw is None:
            return None
        return int(raw)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover with optimistic state update.

        Sets optimistic state immediately so the UI reflects the change without
        waiting for device confirmation.  Reverts on send failure.
        """
        if self._spec.dp_open is None:
            return
        self._optimistic_open = True
        self._optimistic_position = 100 if self._spec.dp_position is not None else None
        self.async_write_ha_state()
        try:
            await self.async_send_dp(self._spec.dp_open.id, True)
        except HomeAssistantError:
            self._optimistic_open = None
            self._optimistic_position = None
            self.async_write_ha_state()
            raise

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover with optimistic state update.

        Sets optimistic state immediately so the UI reflects the change without
        waiting for device confirmation.  Reverts on send failure.
        """
        if self._spec.dp_open is None:
            return
        self._optimistic_open = False
        self._optimistic_position = 0 if self._spec.dp_position is not None else None
        self.async_write_ha_state()
        try:
            await self.async_send_dp(self._spec.dp_open.id, False)
        except HomeAssistantError:
            self._optimistic_open = None
            self._optimistic_position = None
            self.async_write_ha_state()
            raise

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover motor mid-movement.

        No optimistic state is set for stop — the final position is unknown
        until the device reports it.
        """
        if self._spec.dp_stop is not None:
            await self.async_send_dp(self._spec.dp_stop.id, True)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position (0-100) with optimistic update.

        Args:
            **kwargs: Must include ``ATTR_POSITION`` (int 0-100).
        """
        if self._spec.dp_position is None:
            return
        position: int = max(0, min(100, int(kwargs[ATTR_POSITION])))
        self._optimistic_position = position
        self._optimistic_open = position > 0
        self.async_write_ha_state()
        try:
            await self.async_send_dp(self._spec.dp_position.id, position)
        except HomeAssistantError:
            self._optimistic_position = None
            self._optimistic_open = None
            self.async_write_ha_state()
            raise

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the tilt position (0-100) with optimistic update.

        Args:
            **kwargs: Must include ``ATTR_TILT_POSITION`` (int 0-100).
        """
        if self._spec.dp_tilt is None:
            return
        tilt: int = max(0, min(100, int(kwargs[ATTR_TILT_POSITION])))
        self._optimistic_tilt = tilt
        self.async_write_ha_state()
        try:
            await self.async_send_dp(self._spec.dp_tilt.id, tilt)
        except HomeAssistantError:
            self._optimistic_tilt = None
            self.async_write_ha_state()
            raise
