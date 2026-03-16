"""Base entity class for Tuya Cloudless integration.

All platform entities (switch, light, sensor) inherit from
:class:`TuyaCloudlessEntity`.
"""

from __future__ import annotations

__all__ = ["TuyaCloudlessEntity"]

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TuyaCloudlessCoordinator


class TuyaCloudlessEntity(CoordinatorEntity[TuyaCloudlessCoordinator]):
    """Base entity for all Tuya Cloudless entities.

    Provides:
      - Device info linked to the coordinator's gwId.
      - DPS accessor helpers.
      - ``available`` property based on coordinator connection state.

    Subclasses must set ``_attr_unique_id`` in ``__init__``.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TuyaCloudlessCoordinator,
        dp_id: str | None = None,
    ) -> None:
        """Initialise the entity.

        Args:
            coordinator: The device coordinator.
            dp_id: Data point ID this entity maps to (e.g. "1" for main switch).
        """
        super().__init__(coordinator)
        self._dp_id = dp_id

    @property
    def available(self) -> bool:
        """Return True if the device is reachable."""
        return self.coordinator.state.available

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry info from the single canonical source (PLAT-714).

        DeviceInfo is constructed once in ``async_setup_entry`` and stored on
        the coordinator so that all entities always return the same object.
        """
        return self.coordinator.device_info

    def get_dp(self, dp_id: str | None = None) -> Any:
        """Return the current value of a data point.

        Args:
            dp_id: DP key to look up. Defaults to ``self._dp_id``.

        Returns:
            Current DP value, or ``None`` if unavailable.
        """
        key = dp_id or self._dp_id
        if key is None:
            return None
        return self.coordinator.state.dps.get(key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw DP id and current value for diagnostics."""
        if self._dp_id is None:
            return {}
        return {
            "dp_id": self._dp_id,
            "raw_value": self.coordinator.state.dps.get(self._dp_id),
        }

    async def async_send_dp(self, dp_id: str, value: Any) -> None:
        """Send a single DP value to the device.

        Args:
            dp_id: DP key to set.
            value: New value (bool, int, or str depending on device spec).
        """
        await self.coordinator.async_send_dps({dp_id: value})
