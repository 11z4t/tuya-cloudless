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
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
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


class TuyaCloudlessCover(TuyaCloudlessEntity, CoverEntity):
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
        super().__init__(coordinator, dp_id=dp_id)
        self._spec = spec
        self._attr_unique_id = f"{coordinator._gw_id}_{spec.platform}_{spec.name}"
        self._attr_translation_key = spec.name

        # Build supported features based on available DPs in spec
        features = CoverEntityFeature(0)
        if spec.dp_open is not None:
            features |= CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if spec.dp_position is not None:
            features |= CoverEntityFeature.SET_POSITION
        if spec.dp_tilt is not None:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features

        if spec.device_class:
            with contextlib.suppress(ValueError):
                self._attr_device_class = CoverDeviceClass(spec.device_class)

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is fully closed.

        Uses position DP when available (position == 0 = closed).
        Falls back to boolean open DP when no position DP is configured.
        """
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
        """Return current position (0 = closed, 100 = fully open), or None."""
        if self._spec.dp_position is None:
            return None
        raw = self.get_dp(self._spec.dp_position.id)
        if raw is None:
            return None
        return int(raw)

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return current tilt position (0 = closed, 100 = open), or None."""
        if self._spec.dp_tilt is None:
            return None
        raw = self.get_dp(self._spec.dp_tilt.id)
        if raw is None:
            return None
        return int(raw)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        if self._spec.dp_open is not None:
            await self.async_send_dp(self._spec.dp_open.id, True)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        if self._spec.dp_open is not None:
            await self.async_send_dp(self._spec.dp_open.id, False)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position (0-100).

        Args:
            **kwargs: Must include ``ATTR_POSITION`` (int 0-100).
        """
        if self._spec.dp_position is None:
            return
        position: int = kwargs[ATTR_POSITION]
        await self.async_send_dp(self._spec.dp_position.id, position)

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the tilt position (0-100).

        Args:
            **kwargs: Must include ``ATTR_TILT_POSITION`` (int 0-100).
        """
        if self._spec.dp_tilt is None:
            return
        tilt: int = kwargs[ATTR_TILT_POSITION]
        await self.async_send_dp(self._spec.dp_tilt.id, tilt)
