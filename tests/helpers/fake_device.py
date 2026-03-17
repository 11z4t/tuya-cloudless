"""In-process fake Tuya TCP device for integration tests.

Listens on a random loopback port and implements the minimal Tuya LAN
protocol required to test the coordinator:
  - Responds to HEARTBEAT frames with heartbeat ACKs.
  - Responds to CONTROL frames with a status response.
  - Can push DPS updates to a connected client via ``push_dps()``.

Usage::

    device = FakeTuyaDevice("gw001", b"0123456789abcdef", version="3.3")
    port = await device.start()

    coord = TuyaCloudlessCoordinator(..., port=port)
    await coord.async_start()

    await device.push_dps({"1": True})
    await asyncio.sleep(0.1)
    assert coord.state.dps["1"] is True

    await coord.async_stop()
    await device.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class FakeTuyaDevice:
    """Minimal in-process Tuya LAN device over loopback TCP.

    Attributes:
        gw_id:          Device gateway ID (used for logging).
        local_key:      16-byte device local key (same as coordinator's key).
        version:        Protocol version string (e.g. "3.3").
        dps:            Current DPS state dict (mutable).
        received_dps:   Log of all CONTROL DPS dicts received from the client.
    """

    def __init__(
        self,
        gw_id: str,
        local_key: bytes,
        version: str = "3.3",
    ) -> None:
        """Initialise the fake device.

        Args:
            gw_id: Device gateway ID.
            local_key: 16-byte local key (must match the coordinator's key).
            version: Protocol version string.
        """
        self.gw_id = gw_id
        self.local_key = local_key
        self.version = version
        self.dps: dict[str, Any] = {"1": False}
        self.received_dps: list[dict[str, Any]] = []

        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._sequence: int = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> int:
        """Start the TCP server on a random loopback port.

        Returns:
            The TCP port number the server is listening on.
        """
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        port: int = self._server.sockets[0].getsockname()[1]
        _LOGGER.debug("[fake:%s] Listening on 127.0.0.1:%d", self.gw_id, port)
        return port

    async def stop(self) -> None:
        """Stop the TCP server and close any open client connection."""
        # Close the server-side writer first so the coordinator receives EOF
        # and closes its own connection — this unblocks server.wait_closed().
        if self._writer is not None:
            with contextlib.suppress(OSError):
                self._writer.close()
                await self._writer.wait_closed()
            self._writer = None
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            self._server = None

    # ── Push interface ────────────────────────────────────────────────────────

    async def push_dps(self, dps: dict[str, Any]) -> None:
        """Push a DPS update to the connected coordinator.

        Args:
            dps: DPS dict to push (e.g. ``{"1": True}``).

        Raises:
            RuntimeError: If no client is currently connected.
        """
        if self._writer is None:
            raise RuntimeError("No client connected to fake device")

        from tuya_cloudless.protocol import encode_status_response

        self.dps.update(dps)
        frame = encode_status_response(
            dps,
            sequence=self._next_sequence(),
            version=self.version,
            local_key=self.local_key,
        )
        self._writer.write(frame)
        await self._writer.drain()
        _LOGGER.debug("[fake:%s] Pushed DPS: %s", self.gw_id, dps)

    # ── TCP handler ───────────────────────────────────────────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Accept a single client connection and handle frames until EOF."""
        self._writer = writer
        _LOGGER.debug("[fake:%s] Client connected", self.gw_id)

        try:
            await self._read_loop(reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            _LOGGER.debug("[fake:%s] Client disconnected", self.gw_id)
            self._writer = None
            with contextlib.suppress(OSError):
                writer.close()
                await writer.wait_closed()

    async def _read_loop(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read frames and respond until the connection is closed."""
        from tuya_cloudless.const import CMD_CONTROL, CMD_DP_QUERY, CMD_HEARTBEAT, CMD_STATUS
        from tuya_cloudless.protocol import (
            decode_frame,
            encode_heartbeat,
            encode_status_response,
            split_frames,
        )

        buffer = b""
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            except TimeoutError:
                continue
            if not chunk:
                break

            buffer += chunk
            frames, buffer = split_frames(buffer)

            for raw_frame in frames:
                try:
                    frame = decode_frame(
                        raw_frame,
                        version=self.version,
                        local_key=self.local_key,
                    )
                except Exception as exc:
                    _LOGGER.debug("[fake:%s] Could not decode frame: %s", self.gw_id, exc)
                    continue

                if frame.command == CMD_HEARTBEAT:
                    # Echo heartbeat back
                    ack = encode_heartbeat(
                        sequence=self._next_sequence(),
                        version=self.version,
                        local_key=self.local_key,
                    )
                    writer.write(ack)
                    await writer.drain()
                    _LOGGER.debug("[fake:%s] Heartbeat ACK sent", self.gw_id)

                elif frame.command == CMD_CONTROL:
                    # Apply received DPS and respond with status
                    try:
                        payload_dps = frame.dps.get("dps", {})
                        self.dps.update(payload_dps)
                        self.received_dps.append(dict(payload_dps))
                    except Exception:
                        pass
                    response = encode_status_response(
                        self.dps,
                        sequence=self._next_sequence(),
                        version=self.version,
                        local_key=self.local_key,
                    )
                    writer.write(response)
                    await writer.drain()

                elif frame.command in (CMD_DP_QUERY, CMD_STATUS):
                    # Respond with current DPS snapshot (PLAT-778/PLAT-781).
                    # Enables coordinator initial DP_QUERY pre-population and
                    # profile auto-detection in tests.
                    response = encode_status_response(
                        self.dps,
                        sequence=self._next_sequence(),
                        version=self.version,
                        local_key=self.local_key,
                    )
                    writer.write(response)
                    await writer.drain()
                    _LOGGER.debug("[fake:%s] DP_QUERY/STATUS response sent", self.gw_id)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _next_sequence(self) -> int:
        """Return the next sequence number."""
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        return self._sequence
