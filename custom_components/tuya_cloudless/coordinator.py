"""DataUpdateCoordinator for Tuya Cloudless devices.

Manages a persistent TCP connection to a single Tuya device.
Pushes DPS state updates to all subscribed HA entities.

Architecture:
  - One coordinator per config entry (per device).
  - TCP connection maintained in background with exponential back-off reconnect.
  - Heartbeats sent every 20 s to detect silent disconnects.
  - DPS state held in ``state`` dict; listeners notified on every update.
  - v3.4/3.5: ECDH session key negotiated on every new connection.
  - Frame error recovery: ≥5 consecutive decode errors triggers reconnect.
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
    CONF_OPT_COMMAND_TIMEOUT,
    CONF_OPT_HEARTBEAT_INTERVAL,
    CONF_OPT_RECONNECT_MAX_DELAY,
    CONF_PROTOCOL_VERSION,
    DEFAULT_OPT_COMMAND_TIMEOUT,
    DEFAULT_OPT_HEARTBEAT_INTERVAL,
    DEFAULT_OPT_RECONNECT_MAX_DELAY,
    DEFAULT_TCP_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

#: Number of consecutive frame decode errors before triggering reconnect
_MAX_CONSECUTIVE_ERRORS = 5

#: Maximum receive buffer — forces reconnect if exceeded (guards against corrupt streams)
_MAX_BUFFER_BYTES = 65_536  # 64 KB


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
        port: TCP port (default 6668; overridable for tests).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        gw_id: str,
        ip_address: str,
        local_key: str,
        version: str,
        port: int = DEFAULT_TCP_PORT,
        heartbeat_interval: float = DEFAULT_OPT_HEARTBEAT_INTERVAL,
        command_timeout: float = DEFAULT_OPT_COMMAND_TIMEOUT,
        reconnect_max_delay: float = DEFAULT_OPT_RECONNECT_MAX_DELAY,
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
        self._port = port
        self._heartbeat_interval = heartbeat_interval
        self._command_timeout = command_timeout
        self._reconnect_max_delay = reconnect_max_delay

        self.state = DeviceState()
        self._sequence: int = 0
        self._session_key: bytes | None = None
        self._consecutive_decode_errors: int = 0

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None

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
                translation_domain="tuya_cloudless",
                translation_key="device_unavailable",
            )

        try:
            await asyncio.wait_for(
                self._do_send_dps(dps),
                timeout=self._command_timeout,
            )
        except TimeoutError as exc:
            raise HomeAssistantError(
                translation_domain="tuya_cloudless",
                translation_key="command_timeout",
            ) from exc

    async def _do_send_dps(self, dps: dict[str, Any]) -> None:
        """Internal: encode and write DPS control frame."""
        from homeassistant.exceptions import HomeAssistantError

        from tuya_cloudless.protocol import encode_control

        if self._writer is None:
            raise HomeAssistantError(
                translation_domain="tuya_cloudless",
                translation_key="device_unavailable",
            )
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
        from tuya_cloudless.const import RECONNECT_INITIAL_DELAY

        delay = RECONNECT_INITIAL_DELAY
        _first_attempt = True
        while True:
            if not _first_attempt:
                # Count every reconnect attempt (successful or not)
                self.state.reconnect_count += 1
            _first_attempt = False

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
                self.async_update_listeners()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay)

    async def _connect(self) -> None:
        """Establish TCP connection and run receive loop until disconnected."""
        from tuya_cloudless.const import TCP_CONNECT_TIMEOUT

        _LOGGER.info("[%s] Connecting to %s:%d", self._gw_id, self._ip, self._port)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._ip, self._port),
            timeout=TCP_CONNECT_TIMEOUT,
        )
        self._reader = reader
        self._writer = writer

        # For v3.4/3.5: negotiate ECDH session key before sending any commands
        if self._version in ("3.4", "3.5"):
            try:
                self._session_key = await self._negotiate_session_key(reader, writer)
                _LOGGER.info("[%s] Session key established for %s", self._gw_id, self._version)
            except (TuyaCloudlessError, OSError, TimeoutError) as exc:
                _LOGGER.warning(
                    "[%s] Session key negotiation failed: %s — reconnecting",
                    self._gw_id,
                    exc,
                )
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
                raise
        else:
            self._session_key = None

        self._consecutive_decode_errors = 0
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
        self._session_key = None
        self.state.available = False

    async def _heartbeat_loop(self) -> None:
        """Send heartbeat frames at the configured interval."""
        from tuya_cloudless.protocol import encode_heartbeat

        while True:
            await asyncio.sleep(self._heartbeat_interval)
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
        from tuya_cloudless.const import TCP_READ_BUFFER_SIZE, TCP_RECEIVE_TIMEOUT
        from tuya_cloudless.protocol import decode_frame, split_frames

        buffer = b""
        while True:
            try:
                chunk = await asyncio.wait_for(
                    reader.read(TCP_READ_BUFFER_SIZE), timeout=TCP_RECEIVE_TIMEOUT
                )
            except TimeoutError:
                _LOGGER.debug("[%s] Receive timeout — checking connection", self._gw_id)
                continue
            if not chunk:
                _LOGGER.info("[%s] TCP connection closed by device", self._gw_id)
                break

            buffer += chunk
            if len(buffer) > _MAX_BUFFER_BYTES:
                _LOGGER.warning(
                    "[%s] Receive buffer exceeded %d bytes — forcing reconnect",
                    self._gw_id,
                    _MAX_BUFFER_BYTES,
                )
                if self._writer is not None:
                    self._writer.close()
                return
            frames, buffer = split_frames(buffer)

            for raw_frame in frames:
                try:
                    frame = decode_frame(
                        raw_frame,
                        version=self._version,
                        local_key=self._local_key,
                        session_key=self._session_key,
                    )
                    if self._consecutive_decode_errors > 0:
                        self._consecutive_decode_errors = 0
                        self._clear_auth_repair_issue()
                    self._on_frame(frame)
                except (TuyaCloudlessError, ValueError) as exc:
                    self._consecutive_decode_errors += 1
                    _LOGGER.debug(
                        "[%s] Frame decode error #%d: %s",
                        self._gw_id,
                        self._consecutive_decode_errors,
                        exc,
                    )
                    if self._consecutive_decode_errors >= _MAX_CONSECUTIVE_ERRORS:
                        _LOGGER.warning(
                            "[%s] %d consecutive decode errors — forcing reconnect",
                            self._gw_id,
                            self._consecutive_decode_errors,
                        )
                        self._raise_auth_repair_issue()
                        # Close TCP stream to trigger reconnect loop
                        if self._writer is not None:
                            self._writer.close()
                        return

    def _raise_auth_repair_issue(self) -> None:
        """Create an HA repair issue directing the user to re-authenticate."""
        from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

        async_create_issue(
            self.hass,
            DOMAIN,
            f"auth_failed_{self._entry_id}",
            is_fixable=True,
            severity=IssueSeverity.ERROR,
            translation_key="auth_failed",
            translation_placeholders={"device_name": self._gw_id},
        )

    def _clear_auth_repair_issue(self) -> None:
        """Remove the auth failure repair issue once communication is healthy."""
        from homeassistant.helpers.issue_registry import async_delete_issue

        async_delete_issue(self.hass, DOMAIN, f"auth_failed_{self._entry_id}")

    @callback
    def _on_frame(self, frame: Any) -> None:
        """Handle a decoded frame — update DPS state and notify listeners."""
        try:
            payload = frame.dps
        except AttributeError:
            return

        # Tuya frames carry DPS nested under a "dps" key: {"dps": {"1": true}}
        dps: dict[str, Any] = payload.get("dps", {}) if isinstance(payload, dict) else {}
        if dps:
            self.state.dps.update(dps)
            self.state.last_seen = datetime.now(UTC)
            _LOGGER.debug("[%s] DPS update: %s", self._gw_id, list(dps.keys()))
            self.async_set_updated_data(self.state.dps)

    # ── Session key negotiation (v3.4/3.5) ───────────────────────────────────

    async def _negotiate_session_key(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bytes:
        """Perform ECDH session key exchange for protocol v3.4/3.5.

        Protocol:
          1. Generate X25519 ephemeral key pair.
          2. Send CMD_SESS_KEY_NEG_START with our 32-byte public key.
          3. Read device's CMD_SESS_KEY_NEG_RESPONSE with its 32-byte public key.
          4. Compute shared session key via ECDH + HMAC-SHA256.
          5. Send CMD_SESS_KEY_NEG_FINISH to confirm.

        Args:
            reader: Async stream reader for the open TCP connection.
            writer: Async stream writer for the open TCP connection.

        Returns:
            16-byte AES session key derived from ECDH shared secret.

        Raises:
            TuyaCloudlessError: If negotiation fails or times out.
        """
        _MAX_NEG_ATTEMPTS = 3
        last_exc: Exception | None = None

        for attempt in range(_MAX_NEG_ATTEMPTS):
            if attempt:
                _LOGGER.debug(
                    "[%s] Session key negotiation attempt %d/%d",
                    self._gw_id,
                    attempt + 1,
                    _MAX_NEG_ATTEMPTS,
                )
                await asyncio.sleep(0.5)

            try:
                return await self._negotiate_session_key_once(reader, writer)
            except (TuyaCloudlessError, OSError, TimeoutError) as exc:
                last_exc = exc

        raise TuyaCloudlessError(
            f"Session key negotiation failed after {_MAX_NEG_ATTEMPTS} attempts"
        ) from last_exc

    async def _negotiate_session_key_once(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bytes:
        """Single attempt at ECDH session key exchange."""
        from tuya_cloudless.crypto import derive_session_key, generate_ecdh_keypair
        from tuya_cloudless.protocol import encode_session_key_finish, encode_session_key_start

        keypair = generate_ecdh_keypair()

        # Step 1 — send our public key
        start_frame = encode_session_key_start(
            keypair.public_key_bytes,
            sequence=self._next_sequence(),
            local_key=self._local_key,
        )
        writer.write(start_frame)
        await writer.drain()

        # Step 2 — read device public key
        try:
            raw = await asyncio.wait_for(reader.read(256), timeout=5.0)
        except TimeoutError as exc:
            raise TuyaCloudlessError(
                "Session key negotiation timed out waiting for device response"
            ) from exc

        if not raw:
            raise TuyaCloudlessError("Session key negotiation failed: device closed connection")

        # Extract device public key from response payload
        # The response frame carries the device's 32-byte X25519 public key
        # We use the raw bytes after the 16-byte frame header
        from tuya_cloudless.protocol import decode_frame, split_frames

        frames, _ = split_frames(raw)
        if not frames:
            raise TuyaCloudlessError(
                "Session key negotiation: could not parse device response frame"
            )

        # Decode using ECB (no session key yet for negotiation frames)
        resp_frame = decode_frame(
            frames[0],
            version="3.3",  # Decode negotiation frames as ECB
            local_key=self._local_key,
            session_key=None,
        )
        device_pubkey = resp_frame.payload[:32]
        if len(device_pubkey) < 32:
            raise TuyaCloudlessError(
                f"Session key negotiation: device public key too short "
                f"({len(device_pubkey)} bytes, expected 32)"
            )

        # Step 3 — derive session key
        session_key = derive_session_key(keypair.private_key, device_pubkey, self._local_key)

        # Step 4 — send FINISH confirmation
        import hashlib
        import hmac

        confirmation = hmac.new(session_key, device_pubkey, hashlib.sha256).digest()
        finish_frame = encode_session_key_finish(
            confirmation,
            sequence=self._next_sequence(),
            local_key=self._local_key,
            session_key=session_key,
        )
        writer.write(finish_frame)
        await writer.drain()

        _LOGGER.debug("[%s] Session key negotiated (key length=%d)", self._gw_id, len(session_key))
        return session_key

    # ── Utility ───────────────────────────────────────────────────────────────

    def _next_sequence(self) -> int:
        """Return the next monotonically increasing sequence number."""
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        return self._sequence

    @classmethod
    def from_config_entry(cls, hass: HomeAssistant, entry: Any) -> TuyaCloudlessCoordinator:
        """Construct a coordinator from a config entry.

        Args:
            hass: Home Assistant instance.
            entry: Config entry with data fields.

        Returns:
            New :class:`TuyaCloudlessCoordinator`.
        """
        opts = entry.options or {}
        return cls(
            hass=hass,
            entry_id=entry.entry_id,
            gw_id=entry.data[CONF_GW_ID],
            ip_address=entry.data[CONF_IP_ADDRESS],
            local_key=entry.data[CONF_LOCAL_KEY],
            version=entry.data.get(CONF_PROTOCOL_VERSION, "3.3"),
            heartbeat_interval=float(
                opts.get(CONF_OPT_HEARTBEAT_INTERVAL, DEFAULT_OPT_HEARTBEAT_INTERVAL)
            ),
            command_timeout=float(opts.get(CONF_OPT_COMMAND_TIMEOUT, DEFAULT_OPT_COMMAND_TIMEOUT)),
            reconnect_max_delay=float(
                opts.get(CONF_OPT_RECONNECT_MAX_DELAY, DEFAULT_OPT_RECONNECT_MAX_DELAY)
            ),
        )
