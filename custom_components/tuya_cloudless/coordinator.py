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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from tuya_cloudless.exceptions import CryptoError, ProtocolError, TuyaCloudlessError

from .const import (
    CONF_GW_ID,
    CONF_IP_ADDRESS,
    CONF_LOCAL_KEY,
    CONF_OPT_COMMAND_TIMEOUT,
    CONF_OPT_HEARTBEAT_INTERVAL,
    CONF_OPT_RECONNECT_MAX_DELAY,
    CONF_PROTOCOL_VERSION,
    CONNECTIVITY_ISSUE_THRESHOLD,
    DEFAULT_OPT_COMMAND_TIMEOUT,
    DEFAULT_OPT_HEARTBEAT_INTERVAL,
    DEFAULT_OPT_RECONNECT_MAX_DELAY,
    DEFAULT_TCP_PORT,
    DOMAIN,
    EVENT_TUYA_CONNECTED,
    EVENT_TUYA_DISCONNECTED,
    EVENT_TUYA_DP_CHANGED,
)

_LOGGER = logging.getLogger(__name__)

#: Number of consecutive frame decode errors before triggering reconnect
_MAX_CONSECUTIVE_ERRORS = 5

#: Maximum receive buffer — forces reconnect if exceeded (guards against corrupt streams).
# R34-6: Must be larger than MAX_PAYLOAD_SIZE (65536) + FRAME_HEADER_SIZE (16) +
# max checksum (32 HMAC-SHA256) + FRAME_SUFFIX_SIZE (4) = 65588 bytes, otherwise a
# valid max-size frame arriving in multiple TCP chunks triggers a spurious reconnect
# before the full frame can be assembled.  128 KiB gives comfortable headroom.
_MAX_BUFFER_BYTES = 128 * 1024  # 128 KB

#: Maximum number of distinct DP keys allowed per device (prevents memory exhaustion
#: from a malicious device flooding the coordinator with arbitrary key names).
_MAX_DPS_KEYS = 256
#: Maximum byte length of a single DPS key string (Tuya DP IDs are short alphanumeric labels).
_MAX_DPS_KEY_LEN = 64
#: Maximum byte length of a string DP value (guards against oversized strings from rogue devices).
_MAX_DPS_STR_VALUE_LEN = 4096

#: Initial DP_QUERY retry policy (PLAT-761)
_DP_QUERY_MAX_RETRIES: int = 3
_DP_QUERY_RETRY_DELAY: float = 2.0

#: Timeout (seconds) for each individual read in the v3.4/3.5 ECDH session key
#: negotiation (ROB-002 / PLAT-831).  Applies separately to the header read and
#: to the frame-body read within ``_negotiate_session_key_once``.
_SESSION_KEY_NEG_TIMEOUT: float = 5.0

#: Protocol version used when decoding session key negotiation frames.
#: Negotiation frames use ECB decrypt (v3.1/3.2 scheme) without the v3.3+
#: header padding, even for v3.4/3.5 devices.  This is the Tuya protocol
#: specification — do not change without verifying against real hardware.
_SESSION_KEY_NEG_DECODE_VERSION: str = "3.2"

#: PLAT-767 — IP auto-recovery via UDP discovery
#: Trigger a UDP broadcast scan after this many consecutive connection failures.
_IP_REDISCOVER_THRESHOLD: int = 3
#: Seconds to listen on UDP 6666/6667 for the device to announce itself.
_IP_REDISCOVER_TIMEOUT: float = 15.0


@dataclass
class DeviceState:
    """Runtime state for a connected Tuya device.

    Attributes:
        available:      True when TCP connection is up.
        dps:            Live DPS dict (str key → value), mutated in-place on
                        each device update.  Use ``coordinator.data`` for a
                        stable per-update snapshot, or ``get_dp()`` on entities.
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

    # Class-level default so test helpers that create instances via ``__new__``
    # (bypassing ``__init__``) still find the attribute on the object.
    detected_dp_ids: frozenset[str] = frozenset()

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        gw_id: str,
        ip_address: str,
        local_key: str,
        version: str,
        device_info: DeviceInfo,
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
        # R48-F4: Validate key length eagerly so misconfigured integrations or
        # programmatic use with a wrong-length key surfaces at construction time
        # rather than deep inside the async connect path as a confusing KeyDerivationError.
        if len(self._local_key) != 16:
            raise ValueError(f"local_key must be exactly 16 bytes, got {len(self._local_key)}")
        self._version = version
        self._port = port
        self._heartbeat_interval = heartbeat_interval
        self._command_timeout = command_timeout
        self._reconnect_max_delay = reconnect_max_delay

        # Single canonical source of device metadata — declared here and never
        # duplicated elsewhere (PLAT-771). All entities read this via
        # TuyaCloudlessEntity.device_info which delegates to coordinator.device_info.
        self.device_info: DeviceInfo = device_info

        # User-visible device name and profile (set by async_setup_entry after init)
        self.device_name: str = gw_id
        self.profile_name: str = ""

        self.state = DeviceState()
        self._sequence: int = 0
        self._session_key: bytes | None = None
        self._consecutive_decode_errors: int = 0
        self._consecutive_connection_failures: int = 0

        #: DP IDs observed in the first DP_QUERY response — populated once on
        #: first successful data frame.  Used by diagnostics and auto-detection.
        self.detected_dp_ids: frozenset[str] = frozenset()

        self._send_lock: asyncio.Lock = asyncio.Lock()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start the connection manager."""
        # ROB-001 (PLAT-830): Guard against duplicate connection loops.
        # If a connect task is already running (e.g. async_start called twice),
        # do nothing — the existing loop will handle reconnects on its own.
        if self._connect_task is not None and not self._connect_task.done():
            return  # Already connecting/connected

        _LOGGER.info("[%s] Starting coordinator for %s", self._gw_id, self._ip)
        self._connect_task = self.hass.async_create_task(
            self._connection_loop(), name=f"tuya-cloudless-connect:{self._gw_id}"
        )

    async def async_stop(self) -> None:
        """Stop the coordinator and close the TCP connection."""
        _LOGGER.info("[%s] Stopping coordinator", self._gw_id)
        for task in (self._connect_task, self._heartbeat_task):
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

        async with self._send_lock:
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
            except (OSError, TuyaCloudlessError) as exc:
                # OSError: TCP write failed (connection dropped between check and write).
                # TuyaCloudlessError: encode_control raised CryptoError (e.g. missing
                # session key for v3.4/3.5 during reconnect).
                raise HomeAssistantError(
                    translation_domain="tuya_cloudless",
                    translation_key="send_failed",
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
                self._consecutive_connection_failures = 0
                self._clear_connectivity_repair_issue()
            except asyncio.CancelledError:
                return
            except (TimeoutError, OSError, TuyaCloudlessError) as exc:
                self.state.available = False
                # R43-F4: Truncate error string to avoid surfacing full OS
                # error messages (which can embed IP:port) in the HA repair UI
                # and diagnostics downloads.  Keep enough context for support.
                self.state.last_error = str(exc)[:200]
                self._consecutive_connection_failures += 1
                _LOGGER.warning(
                    "[%s] Connection failed: %s — retrying in %.0fs",
                    self._gw_id,
                    exc,
                    delay,
                )
                if self._consecutive_connection_failures >= CONNECTIVITY_ISSUE_THRESHOLD:
                    self._raise_connectivity_repair_issue()
                self.async_update_listeners()
                # PLAT-767: After _IP_REDISCOVER_THRESHOLD consecutive failures,
                # scan UDP to find the device at its new DHCP-assigned IP.
                if (
                    self._consecutive_connection_failures > 0
                    and self._consecutive_connection_failures % _IP_REDISCOVER_THRESHOLD == 0
                ):
                    try:
                        await self._try_rediscover_ip()
                    except asyncio.CancelledError:
                        return
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                delay = min(delay * 2, self._reconnect_max_delay)

    async def _connect(self) -> None:
        """Establish TCP connection and run receive loop until disconnected."""
        from tuya_cloudless.const import TCP_CONNECT_TIMEOUT

        # Reset per-session counters at the start of each TCP connection attempt.
        # _sequence: firmware expects the counter to restart at 1 on a fresh connection.
        # _consecutive_decode_errors: R37-4 — reset here (not after ECDH) so that a
        # failed ECDH negotiation on a previous attempt cannot leave a non-zero error
        # count that would cause a single decode error on the next healthy connection
        # to prematurely trigger reconnect.
        self._sequence = 0
        self._consecutive_decode_errors = 0

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
                # Clear instance refs so concurrent async_send_dps calls cannot
                # observe stale closed streams before the next reconnect attempt.
                self._reader = None
                self._writer = None
                raise
        else:
            self._session_key = None

        self._consecutive_decode_errors = 0
        self.state.last_error = None
        _LOGGER.info("[%s] Connected to %s", self._gw_id, self._ip)

        # Send initial DP_QUERY BEFORE marking available so entity commands cannot
        # race the unlocked initial write (R35-1).  available=True + listener notify
        # are deferred to after the query completes.
        await self._send_initial_dp_query(writer)

        self.state.available = True
        self.async_update_listeners()
        self._fire_event(EVENT_TUYA_CONNECTED)

        # Start heartbeat
        self._heartbeat_task = self.hass.async_create_task(
            self._heartbeat_loop(), name=f"tuya-cloudless-hb:{self._gw_id}"
        )

        try:
            await self._receive_loop(reader)
        finally:
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
            await self._disconnect()

    async def _disconnect(self) -> None:
        """Close TCP connection if open."""
        # R32-1: Fire disconnected event and notify listeners immediately, even
        # when _receive_loop already set _writer=None before calling _disconnect
        # (force-reconnect paths). Without this, EVENT_TUYA_DISCONNECTED is
        # silently skipped and HA entities remain "available" until the
        # connection-loop sleep expires.
        was_available = self.state.available
        self._session_key = None
        self.state.available = False
        if was_available:
            self._fire_event(EVENT_TUYA_DISCONNECTED)
            self.async_update_listeners()
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None

    async def _send_initial_dp_query(self, writer: asyncio.StreamWriter) -> None:
        """Send DP_QUERY (0x0a) immediately after connect to pre-populate entity state.

        Retries up to ``_DP_QUERY_MAX_RETRIES`` times with ``_DP_QUERY_RETRY_DELAY``
        seconds between attempts. If all attempts fail, logs a warning and returns
        gracefully — entity state will populate when the device next pushes an update.

        Args:
            writer: Open async stream writer for the device TCP connection.
        """
        from tuya_cloudless.protocol import encode_status_query

        last_exc: OSError | None = None
        for attempt in range(_DP_QUERY_MAX_RETRIES):
            if attempt > 0:
                await asyncio.sleep(_DP_QUERY_RETRY_DELAY)
            try:
                query = encode_status_query(
                    sequence=self._next_sequence(),
                    version=self._version,
                    local_key=self._local_key,
                    session_key=self._session_key,
                )
                writer.write(query)
                await writer.drain()
                _LOGGER.debug(
                    "[%s] DP_QUERY (0x0a) sent at connect (attempt %d/%d)",
                    self._gw_id,
                    attempt + 1,
                    _DP_QUERY_MAX_RETRIES,
                )
                return
            except OSError as exc:
                last_exc = exc
                _LOGGER.debug(
                    "[%s] DP_QUERY failed (attempt %d/%d): %s",
                    self._gw_id,
                    attempt + 1,
                    _DP_QUERY_MAX_RETRIES,
                    exc,
                )

        _LOGGER.warning(
            "[%s] Failed to send initial DP_QUERY after %d attempts: %s — "
            "entities will show 'unknown' until the device pushes an update",
            self._gw_id,
            _DP_QUERY_MAX_RETRIES,
            last_exc,
        )

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
                    session_key=self._session_key,
                )
                async with self._send_lock:
                    # R45-F4: Re-check _writer inside the lock — _disconnect()
                    # may have set it to None while we waited for the lock.
                    if self._writer is None:
                        break
                    self._writer.write(frame)
                    await self._writer.drain()
                _LOGGER.debug("[%s] Heartbeat sent", self._gw_id)
            except (OSError, CryptoError) as exc:
                _LOGGER.warning("[%s] Heartbeat failed: %s — closing connection", self._gw_id, exc)
                if self._writer is not None:
                    self._writer.close()
                    self._writer = None  # R32-2: clear ref so async_send_dps sees unavailable
                break

    async def _receive_loop(self, reader: asyncio.StreamReader) -> None:
        """Read TCP frames and update DPS state until connection drops.

        Uses :class:`~tuya_cloudless.message.MessageBuffer` for proper partial-frame
        TCP reassembly, then decrypts each message payload before dispatching.
        """
        from tuya_cloudless.const import PROTOCOL_31, TCP_READ_BUFFER_SIZE, TCP_RECEIVE_TIMEOUT
        from tuya_cloudless.crypto import ProtocolVersion, decrypt_payload
        from tuya_cloudless.message import MessageBuffer
        from tuya_cloudless.protocol import TuyaFrame

        # MessageBuffer is intentionally local to _receive_loop.
        # It must NOT be hoisted to an instance variable without calling
        # buf.clear() (or recreating it) on each reconnect, to prevent
        # stale bytes from a previous TCP session corrupting the next one.
        msg_buf = MessageBuffer(
            version=ProtocolVersion(self._version),
            local_key=self._local_key,
        )
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

            # Check BEFORE feeding to prevent MessageBuffer.feed() from raising
            # ProtocolError directly (which would bypass the reconnect log below).
            if msg_buf.pending_bytes + len(chunk) > _MAX_BUFFER_BYTES:
                _LOGGER.warning(
                    "[%s] Receive buffer would exceed %d bytes — forcing reconnect",
                    self._gw_id,
                    _MAX_BUFFER_BYTES,
                )
                if self._writer is not None:
                    self._writer.close()
                    self._writer = None
                return
            msg_buf.feed(chunk)

            all_frames_ok = True
            for tuya_msg in msg_buf.messages():
                try:
                    if tuya_msg.payload and self._version != PROTOCOL_31:
                        decrypted = decrypt_payload(
                            self._version,
                            self._local_key,
                            tuya_msg.payload,
                            self._session_key,
                        )
                    else:
                        decrypted = tuya_msg.payload
                    frame = TuyaFrame(
                        sequence=tuya_msg.sequence,
                        command=int(tuya_msg.command),
                        version=self._version,
                        payload=decrypted,
                    )
                    self._on_frame(frame)
                except (TuyaCloudlessError, ValueError) as exc:
                    # ValueError is caught alongside TuyaCloudlessError because
                    # int(tuya_msg.command) can raise ValueError when the raw
                    # command byte is not a valid integer — this is a malformed
                    # frame, not a programming error, so treat it like any other
                    # decode failure and count toward the reconnect threshold.
                    all_frames_ok = False
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
                            self._writer = None
                        return

            # Also count frame-level decode errors from MessageBuffer (e.g. CRC
            # mismatches detected before decryption) — PLAT-727
            buf_errors = msg_buf.pop_error_count()
            if buf_errors > 0:
                all_frames_ok = False
                self._consecutive_decode_errors += buf_errors
                _LOGGER.debug(
                    "[%s] %d buffer-level frame error(s) (total consecutive: %d)",
                    self._gw_id,
                    buf_errors,
                    self._consecutive_decode_errors,
                )
                if self._consecutive_decode_errors >= _MAX_CONSECUTIVE_ERRORS:
                    _LOGGER.warning(
                        "[%s] %d consecutive decode errors — forcing reconnect",
                        self._gw_id,
                        self._consecutive_decode_errors,
                    )
                    self._raise_auth_repair_issue()
                    if self._writer is not None:
                        self._writer.close()
                        self._writer = None
                    return

            # Decay the consecutive error counter on a clean batch (R30-1).
            # Resetting to zero on a single clean recv() would allow an adversarial
            # device to suppress reconnect indefinitely by alternating one good
            # frame with up to four bad frames across separate TCP recv() calls —
            # the counter would oscillate between 4 and 0 and never reach the
            # reconnect threshold of 5.  Decrementing by 1 per clean batch instead
            # means the counter can only drop one step for each error-free recv(),
            # so a sustained attack still accumulates to the reconnect threshold.
            if all_frames_ok and self._consecutive_decode_errors > 0:
                self._consecutive_decode_errors -= 1
                if self._consecutive_decode_errors == 0:
                    self._clear_auth_repair_issue()

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

    def _raise_connectivity_repair_issue(self) -> None:
        """Create an HA repair issue directing the user to check the device IP address."""
        from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

        from .diagnostics import _partial_ip

        async_create_issue(
            self.hass,
            DOMAIN,
            f"connectivity_{self._entry_id}",
            is_fixable=True,
            severity=IssueSeverity.WARNING,
            translation_key="connectivity",
            translation_placeholders={
                "ip_address": _partial_ip(self._ip),
                "name": self._gw_id,
            },
        )

    def _clear_connectivity_repair_issue(self) -> None:
        """Remove the connectivity repair issue once the device connects successfully."""
        from homeassistant.helpers.issue_registry import async_delete_issue

        async_delete_issue(self.hass, DOMAIN, f"connectivity_{self._entry_id}")

    @callback
    def _on_frame(self, frame: Any) -> None:
        """Handle a decoded frame — update DPS state and notify listeners."""
        try:
            payload = frame.dps
        except (AttributeError, ProtocolError) as exc:
            # AttributeError: frame object has no dps property (unexpected frame type)
            # ProtocolError: frame.dps raised because payload is not valid JSON
            _LOGGER.debug("[%s] Received undecodable frame: %s", self._gw_id, exc)
            return

        # Tuya frames carry DPS nested under a "dps" key: {"dps": {"1": true}}
        raw_dps: dict[str, Any] = payload.get("dps", {}) if isinstance(payload, dict) else {}
        # Reject non-scalar DPS values (dict/list) — entities expect bool/int/float/str/None.
        # A misbehaving or malicious device could send a complex JSON object as a DP value
        # which would cause TypeErrors in entity platform code (e.g. int(coordinator.data["2"])).
        # Also reject oversized keys and oversized string values to prevent memory exhaustion.
        _SCALAR = (bool, int, float, str, type(None))
        dps = {
            k: v
            for k, v in raw_dps.items()
            if (
                isinstance(k, str)
                and len(k) <= _MAX_DPS_KEY_LEN
                and isinstance(v, _SCALAR)
                and (not isinstance(v, str) or len(v) <= _MAX_DPS_STR_VALUE_LEN)
            )
        }
        if dps:
            # Guard against a malicious device flooding the coordinator with
            # arbitrary DP key names — unbounded accumulation would exhaust memory.
            # Only block NEW keys that would push past the cap; existing-key
            # updates (state changes for already-known DPs) are always accepted.
            new_keys = [k for k in dps if k not in self.state.dps]
            if len(self.state.dps) + len(new_keys) > _MAX_DPS_KEYS:
                allowed_new = _MAX_DPS_KEYS - len(self.state.dps)
                new_key_set = set(new_keys[:allowed_new]) if allowed_new > 0 else set()
                _LOGGER.warning(
                    "[%s] DPS key cap (%d) reached — accepting %d new keys, "
                    "dropping %d; existing-key updates still applied",
                    self._gw_id,
                    _MAX_DPS_KEYS,
                    len(new_key_set),
                    len(new_keys) - len(new_key_set),
                )
                # Filter frame to existing keys + the capped subset of new keys.
                dps = {k: v for k, v in dps.items() if k in self.state.dps or k in new_key_set}
            # Accumulate DP IDs seen across all frames for auto-detection (PLAT-778).
            # Use union so DPs that arrive in later frames (e.g. a separate status
            # push after the initial DP_QUERY response) are also captured.
            new_ids = frozenset(dps.keys()) - self.detected_dp_ids
            if new_ids:
                self.detected_dp_ids |= new_ids
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "[%s] Auto-detected DP IDs: %s",
                        self._gw_id,
                        sorted(self.detected_dp_ids),
                    )
            self.state.dps.update(dps)
            self.state.last_seen = datetime.now(UTC)
            _LOGGER.debug("[%s] DPS update: %s", self._gw_id, list(dps.keys()))
            # Pass a shallow copy so coordinator.data is a stable snapshot of
            # this update. Callers that hold coordinator.data will not see
            # subsequent mutations of state.dps.
            self.async_set_updated_data(dict(self.state.dps))
            self._fire_event(EVENT_TUYA_DP_CHANGED, {"dps": dict(dps)})

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

        # Step 2 — read device public key.
        # Read the exact number of bytes in the response frame by first reading the
        # 16-byte header, parsing the ``length`` field, then reading the remainder.
        # This replaces the former hardcoded ``reader.read(256)`` (PLAT-779).
        import struct as _struct

        from tuya_cloudless.const import FRAME_HEADER_SIZE, MAX_PAYLOAD_SIZE
        from tuya_cloudless.protocol import decode_frame, split_frames

        try:
            header_bytes = await asyncio.wait_for(
                reader.readexactly(FRAME_HEADER_SIZE), timeout=_SESSION_KEY_NEG_TIMEOUT
            )
            frame_payload_len = _struct.unpack(">I", header_bytes[12:16])[0]
            if frame_payload_len < 8:
                raise TuyaCloudlessError(
                    f"Session key response frame length too small ({frame_payload_len} < 8)"
                )
            if frame_payload_len > MAX_PAYLOAD_SIZE:
                raise TuyaCloudlessError(
                    f"Session key response frame too large ({frame_payload_len} bytes)"
                )
            rest_bytes = await asyncio.wait_for(
                reader.readexactly(frame_payload_len), timeout=_SESSION_KEY_NEG_TIMEOUT
            )
            raw = header_bytes + rest_bytes
        except asyncio.IncompleteReadError as exc:
            raise TuyaCloudlessError(
                "Session key negotiation: closed connection before response completed"
            ) from exc
        except TimeoutError as exc:
            raise TuyaCloudlessError(
                "Session key negotiation timed out waiting for device response"
            ) from exc

        frames, _ = split_frames(raw)
        if not frames:
            raise TuyaCloudlessError(
                "Session key negotiation: could not parse device response frame"
            )

        # Decode using ECB (no session key yet for negotiation frames)
        resp_frame = decode_frame(
            frames[0],
            version=_SESSION_KEY_NEG_DECODE_VERSION,
            local_key=self._local_key,
            session_key=None,
        )
        if len(resp_frame.payload) < 32:
            raise TuyaCloudlessError(
                f"Session key negotiation: device response payload too short "
                f"({len(resp_frame.payload)} bytes, expected at least 32)"
            )
        device_pubkey = resp_frame.payload[:32]

        # Step 3 — derive session key
        session_key = derive_session_key(keypair.private_key, device_pubkey, self._local_key)

        # Step 4 — send FINISH confirmation
        import hashlib
        import hmac

        # R45-F3: Use keyword arguments for hmac.new() to prevent silent bugs if
        # the argument order were ever changed, and to make the intent explicit.
        confirmation = hmac.new(
            key=session_key, msg=device_pubkey, digestmod=hashlib.sha256
        ).digest()
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

    # ── IP auto-recovery (PLAT-767) ───────────────────────────────────────────

    def async_update_ip(self, new_ip: str) -> None:
        """Update the device IP in-place and persist it to the config entry.

        Called automatically when UDP discovery finds the device at a new address
        (e.g. after a DHCP lease renewal).  The next connection attempt in
        ``_connection_loop`` will use the updated ``self._ip``.

        Args:
            new_ip: The newly discovered IPv4 address of the device.
        """
        import ipaddress

        try:
            addr = ipaddress.ip_address(new_ip)
        except ValueError:
            _LOGGER.warning(
                "[%s] UDP rediscovery returned invalid IP %r — ignoring update",
                self._gw_id,
                new_ip[:64],
            )
            return
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
            _LOGGER.warning(
                "[%s] UDP rediscovery returned non-routable IP %r — ignoring update",
                self._gw_id,
                new_ip[:64],
            )
            return
        old_ip = self._ip
        self._ip = new_ip
        _LOGGER.info(
            "[%s] IP address changed: %s → %s — will reconnect to new address",
            self._gw_id,
            old_ip,
            new_ip,
        )
        # Persist so the new IP survives an HA restart
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            self.hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_IP_ADDRESS: new_ip},
            )
        else:
            _LOGGER.warning(
                "[%s] Config entry %s not found — IP update not persisted to storage",
                self._gw_id,
                self._entry_id,
            )

    async def _try_rediscover_ip(self) -> None:
        """Scan UDP 6666/6667 to find the device's new IP after a DHCP change.

        Listens for up to ``_IP_REDISCOVER_TIMEOUT`` seconds.  If the device
        is found at a different address, calls :meth:`async_update_ip` to
        update the in-memory IP and persist to the config entry.

        Errors binding the UDP socket (e.g. permission denied) are logged and
        swallowed so that the normal reconnect loop continues unaffected.
        """
        from tuya_cloudless.discovery import DiscoveryListener
        from tuya_cloudless.exceptions import DiscoveryError

        _LOGGER.debug(
            "[%s] Starting UDP rediscovery (timeout=%.0fs) to locate new IP",
            self._gw_id,
            _IP_REDISCOVER_TIMEOUT,
        )
        listener = DiscoveryListener(known_devices={self._gw_id: self._local_key})

        try:
            await listener.start()
        except DiscoveryError as exc:
            _LOGGER.debug("[%s] UDP rediscovery: could not open socket — %s", self._gw_id, exc)
            return

        try:
            device = await listener.wait_for_device(self._gw_id, timeout=_IP_REDISCOVER_TIMEOUT)
            if device.ip != self._ip:
                self.async_update_ip(device.ip)
            else:
                _LOGGER.debug(
                    "[%s] UDP rediscovery: device still at %s — no IP change",
                    self._gw_id,
                    device.ip,
                )
        except TimeoutError:
            _LOGGER.debug(
                "[%s] UDP rediscovery: device not heard within %.0fs",
                self._gw_id,
                _IP_REDISCOVER_TIMEOUT,
            )
        finally:
            await listener.stop()

    # ── Public accessors ──────────────────────────────────────────────────────

    @property
    def gw_id(self) -> str:
        """Device gateway ID (public accessor)."""
        return self._gw_id

    @property
    def version(self) -> str:
        """Protocol version string, e.g. '3.3' (public accessor)."""
        return self._version

    @property
    def tcp_connected(self) -> bool:
        """True if the TCP writer is open (PLAT-720)."""
        return self._writer is not None

    @property
    def sequence_counter(self) -> int:
        """Current sequence counter value (PLAT-720)."""
        return self._sequence

    @property
    def session_key_active(self) -> bool:
        """True if a v3.4/v3.5 session key has been negotiated (PLAT-720)."""
        return self._session_key is not None

    @property
    def consecutive_decode_errors(self) -> int:
        """Number of consecutive frame-decode errors since last success (PLAT-720)."""
        return self._consecutive_decode_errors

    @property
    def dp_values(self) -> dict[str, Any]:
        """Current DP (data point) snapshot as a plain dict.

        Returns a direct reference to the live state dict; callers must not mutate it.
        Use this instead of accessing ``coordinator.state.dps`` directly.
        """
        return self.state.dps

    @property
    def is_connected(self) -> bool:
        """True if the device is available (TCP connected and initial DPS received).

        This reflects the entity-level availability as tracked by ``DeviceState``.
        Use this instead of accessing ``coordinator.state.available`` directly.
        """
        return self.state.available

    @property
    def ip_address(self) -> str:
        """IP address of the device."""
        return self._ip

    @property
    def last_seen(self) -> datetime | None:
        """UTC timestamp of the last status update received from the device.

        Returns ``None`` if no update has been received since startup.
        """
        return self.state.last_seen

    @property
    def reconnect_count(self) -> int:
        """Total number of TCP reconnection attempts since startup."""
        return self.state.reconnect_count

    @property
    def last_error(self) -> str | None:
        """Human-readable description of the last connection error, or ``None``."""
        return self.state.last_error

    # ── Utility ───────────────────────────────────────────────────────────────

    @callback
    def _fire_event(self, event_type: str, extra_data: dict | None = None) -> None:
        """Fire a Tuya Cloudless event on the HA event bus.

        Includes the HA device registry ID so that device automations can filter
        by device.  If the device is not yet in the registry (e.g. first connect
        before entities are set up) the gw_id is used as a fallback.

        Args:
            event_type: Event type string, one of EVENT_TUYA_*.
            extra_data: Optional additional keys to include in event data.
        """
        from homeassistant.const import ATTR_DEVICE_ID
        from homeassistant.helpers import device_registry as dr

        device = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, self._gw_id)})
        device_id: str = device.id if device is not None else self._gw_id
        data: dict = {ATTR_DEVICE_ID: device_id, CONF_GW_ID: self._gw_id}
        if extra_data:
            data.update(extra_data)
        self.hass.bus.async_fire(event_type, data)

    def _next_sequence(self) -> int:
        """Return the next monotonically increasing sequence number."""
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        return self._sequence

    @classmethod
    def from_config_entry(
        cls,
        hass: HomeAssistant,
        entry: Any,
        device_info: DeviceInfo,
    ) -> TuyaCloudlessCoordinator:
        """Construct a coordinator from a config entry.

        ``device_info`` is built exactly once in ``async_setup_entry`` (after
        the device profile is resolved) and passed here so that it becomes the
        single canonical source.  All entities read it via
        ``TuyaCloudlessEntity.device_info`` which delegates to this attribute.

        Args:
            hass: Home Assistant instance.
            entry: Config entry with data fields.
            device_info: Pre-built :class:`DeviceInfo` for this device.

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
            device_info=device_info,
            heartbeat_interval=float(
                opts.get(CONF_OPT_HEARTBEAT_INTERVAL, DEFAULT_OPT_HEARTBEAT_INTERVAL)
            ),
            command_timeout=float(opts.get(CONF_OPT_COMMAND_TIMEOUT, DEFAULT_OPT_COMMAND_TIMEOUT)),
            reconnect_max_delay=float(
                opts.get(CONF_OPT_RECONNECT_MAX_DELAY, DEFAULT_OPT_RECONNECT_MAX_DELAY)
            ),
        )
