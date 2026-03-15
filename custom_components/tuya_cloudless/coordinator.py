"""DataUpdateCoordinator for Tuya Cloudless devices.

Manages a persistent TCP connection to a single Tuya device.
Pushes DPS state updates to all subscribed HA entities.

Architecture:
  - One coordinator per config entry (per device).
  - TCP connection maintained in background with exponential back-off reconnect.
  - Heartbeats sent every 20 s to detect silent disconnects.
  - DPS state held in ``state`` dict; listeners notified on every update.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from tuya_cloudless.exceptions import TuyaCloudlessError

from .const import (
    CONF_GW_ID,
    CONF_IP_ADDRESS,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_TCP_PORT,
    DOMAIN,
    HEARTBEAT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceState:
    """Runtime state for a connected Tuya device.

    Attributes:
        available:      True when TCP connection is up.
        dps:            Latest DPS snapshot (str key → value).
        last_seen:      UTC timestamp of the last successful response.
        reconnect_count: Total number of reconnections since startup.
        last_error:     Human-readable description of the last error (if any).
    """

    available: bool = False
    dps: dict[str, Any] = field(default_factory=dict)
    last_seen: datetime | None = None
    reconnect_count: int = 0
    last_error: str | None = None


class TuyaCloudlessCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages the TCP connection and DPS state for a single Tuya device.

    This coordinator does NOT use the standard poll interval — it operates
    in push mode: the TCP connection streams DPS updates from the device.
    Heartbeats serve as liveness probes.

    Args:
        hass: Home Assistant instance.
        entry_id: Config entry ID (used for logging context).
        gw_id: Device gateway ID.
        ip_address: Device IP address.
        local_key: 16-byte device local key.
        version: Protocol version string (e.g. "3.3").
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        gw_id: str,
        ip_address: str,
        local_key: str,
        version: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{gw_id}",
        )
        self._entry_id = entry_id
        self._gw_id = gw_id
        self._ip = ip_address
        self._local_key = local_key.encode() if isinstance(local_key, str) else local_key
        self._version = version

        self.state = DeviceState()
        self._sequence: int = 0
        self._session_key: bytes | None = None  # Set after v3.4/3.5 key exchange

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connect_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._read_task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start the connection manager."""
        _LOGGER.info("[%s] Starting coordinator for %s", self._gw_id, self._ip)
        self._connect_task = self.hass.async_create_task(
            self._connection_loop(), name=f"tuya-cloudless-connect:{self._gw_id}"
        )

    async def async_stop(self) -> None:
        """Stop the coordinator and close the TCP connection."""
        _LOGGER.info("[%s] Stopping coordinator", self._gw_id)
        for task in (self._connect_task, self._heartbeat_task, self._read_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._disconnect()

    # ── DPS control ───────────────────────────────────────────────────────────

    async def async_send_dps(self, dps: dict[str, Any]) -> None:
        """Send a DPS control command to the device.

        Args:
            dps: Dict of DP key → value to set, e.g. {"1": True}.

        Raises:
            HomeAssistantError: If the device is unavailable or send fails.
        """
        from homeassistant.exceptions import HomeAssistantError

        if not self.state.available or self._writer is None:
            raise HomeAssistantError(
                f"Device {self._gw_id} is unavailable — cannot send command"
            )

        try:
            await asyncio.wait_for(
                self._do_send_dps(dps),
                timeout=DEFAULT_COMMAND_TIMEOUT,
            )
        except TimeoutError as exc:
            raise HomeAssistantError(
                f"Device {self._gw_id} did not respond within {DEFAULT_COMMAND_TIMEOUT}s"
            ) from exc

    async def _do_send_dps(self, dps: dict[str, Any]) -> None:
        """Internal: encode and write DPS control frame."""
        from tuya_cloudless.protocol import encode_control

        assert self._writer is not None
        frame = encode_control(
            dps,
            sequence=self._next_sequence(),
            version=self._version,
            local_key=self._local_key,
            session_key=self._session_key,
        )
        self._writer.write(frame)
        await self._writer.drain()
        _LOGGER.debug("[%s] Sent DPS: %s", self._gw_id, list(dps.keys()))

    # ── Connection management ─────────────────────────────────────────────────

    async def _connection_loop(self) -> None:
        """Reconnect loop with exponential back-off."""
        from tuya_cloudless.const import RECONNECT_INITIAL_DELAY, RECONNECT_MAX_DELAY

        delay = RECONNECT_INITIAL_DELAY
        while True:
            try:
                await self._connect()
                delay = RECONNECT_INITIAL_DELAY  # Reset on success
            except asyncio.CancelledError:
                return
            except (TimeoutError, OSError, TuyaCloudlessError) as exc:
                self.state.available = False
                self.state.last_error = str(exc)
                _LOGGER.warning(
                    "[%s] Connection failed: %s — retrying in %.0fs",
                    self._gw_id,
                    exc,
                    delay,
                )
                self.state.reconnect_count += 1
                self.async_update_listeners()
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect(self) -> None:
        """Establish TCP connection and run receive loop until disconnected."""
        _LOGGER.info("[%s] Connecting to %s:%d", self._gw_id, self._ip, DEFAULT_TCP_PORT)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._ip, DEFAULT_TCP_PORT),
            timeout=10.0,
        )
        self._reader = reader
        self._writer = writer
        self.state.available = True
        self.state.last_error = None
        _LOGGER.info("[%s] Connected to %s", self._gw_id, self._ip)
        self.async_update_listeners()

        # Start heartbeat
        self._heartbeat_task = self.hass.async_create_task(
            self._heartbeat_loop(), name=f"tuya-cloudless-hb:{self._gw_id}"
        )

        try:
            await self._receive_loop(reader)
        finally:
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            await self._disconnect()

    async def _disconnect(self) -> None:
        """Close TCP connection if open."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None
        self.state.available = False

    async def _heartbeat_loop(self) -> None:
        """Send heartbeat frames at HEARTBEAT_INTERVAL seconds."""
        from tuya_cloudless.protocol import encode_heartbeat

        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self._writer is None:
                break
            try:
                frame = encode_heartbeat(
                    sequence=self._next_sequence(),
                    version=self._version,
                    local_key=self._local_key,
                )
                self._writer.write(frame)
                await self._writer.drain()
                _LOGGER.debug("[%s] Heartbeat sent", self._gw_id)
            except OSError as exc:
                _LOGGER.warning("[%s] Heartbeat failed: %s", self._gw_id, exc)
                break

    async def _receive_loop(self, reader: asyncio.StreamReader) -> None:
        """Read TCP frames and update DPS state until connection drops."""
        from tuya_cloudless.protocol import decode_frame, split_frames

        buffer = b""
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=30.0)
            except TimeoutError:
                _LOGGER.debug("[%s] Receive timeout — checking connection", self._gw_id)
                continue
            if not chunk:
                _LOGGER.info("[%s] TCP connection closed by device", self._gw_id)
                break

            buffer += chunk
            frames, buffer = split_frames(buffer)

            for raw_frame in frames:
                try:
                    frame = decode_frame(
                        raw_frame,
                        version=self._version,
                        local_key=self._local_key,
                        session_key=self._session_key,
                    )
                    self._on_frame(frame)
                except (TuyaCloudlessError, ValueError) as exc:
                    _LOGGER.debug("[%s] Frame decode error: %s", self._gw_id, exc)

    @callback
    def _on_frame(self, frame: Any) -> None:
        """Handle a decoded frame — update DPS state and notify listeners."""
        try:
            dps = frame.dps
        except AttributeError:
            return

        if dps:
            self.state.dps.update(dps)
            self.state.last_seen = datetime.now(UTC)
            _LOGGER.debug("[%s] DPS update: %s", self._gw_id, list(dps.keys()))
            self.async_set_updated_data(self.state.dps)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _next_sequence(self) -> int:
        """Return the next monotonically increasing sequence number."""
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        return self._sequence

    @classmethod
    def from_config_entry(
        cls, hass: HomeAssistant, entry: Any
    ) -> TuyaCloudlessCoordinator:
        """Construct a coordinator from a config entry.

        Args:
            hass: Home Assistant instance.
            entry: Config entry with data fields.

        Returns:
            New :class:`TuyaCloudlessCoordinator`.
        """
        return cls(
            hass=hass,
            entry_id=entry.entry_id,
            gw_id=entry.data[CONF_GW_ID],
            ip_address=entry.data[CONF_IP_ADDRESS],
            local_key=entry.data[CONF_LOCAL_KEY],
            version=entry.data.get(CONF_PROTOCOL_VERSION, "3.3"),
        )
