"""HTTP pairing server for Tuya Cloudless initial device provisioning.

Serves two roles simultaneously:

  1. **Web UI** — serves ``pairing_ui/index.html`` at ``http://ha-host:8099``.
     The page uses Web Bluetooth (Chrome/Edge only) to push WiFi credentials
     and a custom activation URL to the device over BLE.

  2. **Fake-cloud activation endpoint** — after receiving WiFi credentials
     from the browser, the Tuya device connects to WiFi and POSTs to
     ``/api/tuya/device/active`` (the ``activator_url`` encoded in the BLE
     token).  This handler generates a random 16-byte ``local_key``, stores
     the result, and responds with the Tuya activation format the device
     expects.

Activation results are delivered to the browser via Server-Sent Events (SSE)
at ``/api/provision/events``.  The browser can also poll
``GET /api/provision/result/{token}`` for non-SSE clients.

Architecture::

    [Browser]  ─── Web Bluetooth ──►  [Tuya Device]
        │                                   │ (joins WiFi)
        │◄── SSE ──────────────────────────►│
        │                     ▼
        │              POST /api/tuya/device/active
        │                     ▼
        │◄── SSE event ──  [PairingServer]
        │                  generates local_key
        │                  stores ActivationResult

IMPORTANT: This module lives in ``custom_components/`` and may import
``homeassistant.*``.  The ``lib/`` library must NEVER be imported from here
with HA side-effects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from aiohttp import web

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# ── Configuration constants ────────────────────────────────────────────────

#: Default TCP port the pairing server listens on
PAIRING_SERVER_PORT: Final[int] = 8099

#: Maximum seconds an activation result is kept in memory
_RESULT_TTL_SECS: Final[float] = 300.0

#: Path to the static web UI assets (relative to this file)
_UI_DIR: Final[Path] = Path(__file__).parent / "pairing_ui"

#: local_key length in bytes (Tuya standard: 16 bytes → 16 ASCII chars)
_LOCAL_KEY_BYTES: Final[int] = 16


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class ActivationResult:
    """Result stored when a device successfully activates via fake-cloud.

    Attributes:
        gw_id:       Device gateway ID (device's self-reported ID).
        product_key: Product key reported by the device (may be empty).
        local_key:   Generated 16-character ASCII key for local control.
        ip_address:  Device IP address extracted from the HTTP request.
        sw_ver:      Firmware version string reported by the device.
        timestamp:   Unix timestamp when the activation was recorded.
    """

    gw_id: str
    product_key: str
    local_key: str
    ip_address: str
    sw_ver: str = ""
    timestamp: float = field(default_factory=time.monotonic)


# ── PairingServer ─────────────────────────────────────────────────────────────

class PairingServer:
    """Lightweight aiohttp HTTP server for Tuya BLE device provisioning.

    Lifecycle::

        server = PairingServer(hass)
        await server.start()
        # ... HA is running ...
        await server.stop()

    The server is automatically started when the first Tuya Cloudless entry
    is set up and stopped when the last entry is removed.

    Args:
        hass: Home Assistant instance (used for IP address resolution).
        port: TCP port to listen on (default :const:`PAIRING_SERVER_PORT`).
    """

    def __init__(self, hass: HomeAssistant, port: int = PAIRING_SERVER_PORT) -> None:
        """Initialise the pairing server."""
        self._hass = hass
        self._port = port
        self._app: web.Application = self._build_app()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        # token → ActivationResult
        self._results: dict[str, ActivationResult] = {}
        # SSE subscriber queues (one per open /api/provision/events connection)
        self._sse_queues: list[asyncio.Queue[str | None]] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the HTTP server.

        Binds to ``0.0.0.0:{port}`` so the Tuya device can reach the fake-cloud
        endpoint after joining WiFi (the device and HA host must be on the same
        LAN segment).

        Raises:
            OSError: If the port is already in use.
        """
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await self._site.start()
        _LOGGER.info("Tuya Cloudless pairing server listening on port %d", self._port)

    async def stop(self) -> None:
        """Stop the HTTP server and clean up resources."""
        # Signal all SSE subscribers to close
        for q in list(self._sse_queues):
            await q.put(None)
        self._sse_queues.clear()

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
        _LOGGER.info("Tuya Cloudless pairing server stopped")

    # ── Public helpers ─────────────────────────────────────────────────────

    def get_result(self, token: str) -> ActivationResult | None:
        """Return the activation result for *token*, or ``None`` if not yet ready.

        Args:
            token: The provisioning token sent to the device via BLE.

        Returns:
            Completed :class:`ActivationResult` or ``None``.
        """
        self._expire_old_results()
        return self._results.get(token)

    def ha_local_url(self) -> str:
        """Return the HA host URL for use in the pairing tool deep-link.

        Extracts the HA internal URL hostname and appends port 8099.

        Returns:
            Absolute ``http://`` URL string for the pairing server.
        """
        try:
            internal = getattr(self._hass.config, "internal_url", None)
        except AttributeError:
            internal = None

        if isinstance(internal, str) and internal:
            from urllib.parse import urlparse

            parsed = urlparse(internal)
            host = parsed.hostname or "homeassistant.local"
            return f"http://{host}:{self._port}"
        return f"http://homeassistant.local:{self._port}"

    # ── Route handlers ─────────────────────────────────────────────────────

    def _build_app(self) -> web.Application:
        """Build and return the aiohttp Application with all routes registered."""
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/provision/config", self._handle_config)
        app.router.add_get("/api/provision/events", self._handle_sse)
        app.router.add_get("/api/provision/result/{token}", self._handle_get_result)
        app.router.add_post("/api/tuya/device/active", self._handle_activate)
        # Also accept the Tuya cloud API path format some firmware uses
        app.router.add_post("/api.json", self._handle_activate)
        # Serve static assets from pairing_ui/
        if _UI_DIR.is_dir():
            app.router.add_static("/static", _UI_DIR, show_index=False)
        return app

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the pairing UI HTML page.

        Args:
            request: Incoming HTTP request.

        Returns:
            HTML response or 404 if the UI directory is missing.
        """
        index_path = _UI_DIR / "index.html"
        if not index_path.is_file():
            return web.Response(
                status=404,
                text="Pairing UI not found. This is a bug — please report it.",
            )
        return web.Response(
            body=index_path.read_bytes(),
            content_type="text/html",
            charset="utf-8",
        )

    async def _handle_config(self, request: web.Request) -> web.Response:
        """Return pairing server configuration for the browser UI.

        The UI fetches this on load so it always uses the correct
        ``activator_url`` regardless of whether the page was served over
        HTTP (desktop) or HTTPS (mobile via reverse proxy).

        The ``activator_url`` is always an **HTTP** LAN URL because:
          - The Tuya device on LAN cannot verify TLS certificates.
          - The device activation POST must reach this server directly,
            not via a cloud proxy.

        Args:
            request: Incoming HTTP request from the browser.

        Returns:
            JSON: ``{"activator_url": "http://<LAN-IP>:8099",
                     "events_url": "...", "result_url_template": "..."}``
        """
        base = self.ha_local_url()
        return web.json_response(
            {
                "activator_url": base,
                "events_url": f"{base}/api/provision/events",
                "result_url_template": f"{base}/api/provision/result/{{token}}",
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_activate(self, request: web.Request) -> web.Response:
        """Handle the Tuya device activation POST.

        The device calls this endpoint after connecting to WiFi.  The request
        body (JSON) contains the device ID, product key, token, and firmware
        version.  We generate a random ``local_key`` and respond in Tuya cloud
        format so the device accepts it.

        Args:
            request: Incoming HTTP request from the Tuya device.

        Returns:
            JSON response in Tuya cloud activation format.
        """
        client_ip = request.remote or "unknown"
        _LOGGER.debug("Activation request from %s", client_ip)

        try:
            body = await request.json()
        except Exception:
            body = {}

        # Support both top-level and nested ``data`` field
        data: dict[str, object] = body.get("data", body)  # type: ignore[assignment]

        gw_id = str(data.get("gw_id") or data.get("gwId") or "")
        product_key = str(data.get("product_key") or data.get("productKey") or "")
        token = str(data.get("token") or "")
        sw_ver = str(data.get("sw_ver") or data.get("swVer") or "")

        _LOGGER.info(
            "Tuya device activation: gw_id=%s product_key=%s token=%s sw_ver=%s ip=%s",
            gw_id or "<empty>",
            product_key or "<empty>",
            token or "<empty>",
            sw_ver or "<empty>",
            client_ip,
        )

        # Generate a random 16-character local_key (printable ASCII, 32 hex chars)
        local_key = secrets.token_hex(_LOCAL_KEY_BYTES)

        result = ActivationResult(
            gw_id=gw_id,
            product_key=product_key,
            local_key=local_key,
            ip_address=client_ip,
            sw_ver=sw_ver,
        )

        if token:
            self._results[token] = result
            self._expire_old_results()

        # Notify SSE subscribers
        event_data = {
            "gw_id": gw_id,
            "local_key": local_key,
            "ip_address": client_ip,
            "token": token,
        }
        await self._broadcast_sse("activated", json.dumps(event_data))

        # Respond in Tuya cloud activation format
        response_body = {
            "t": int(time.time()),
            "success": True,
            "result": {
                "gwId": gw_id,
                "active": 2,
                "ability": 0,
                "localKey": local_key,
                "timezone": "UTC",
                "netType": 0,
            },
        }
        return web.json_response(response_body)

    async def _handle_get_result(self, request: web.Request) -> web.Response:
        """Poll for an activation result by token.

        Args:
            request: Incoming HTTP request with ``{token}`` path variable.

        Returns:
            JSON response with device info + local_key, or 202 if pending.
        """
        token = request.match_info["token"]
        result = self.get_result(token)

        if result is None:
            return web.json_response({"status": "pending"}, status=202)

        return web.json_response(
            {
                "status": "ok",
                "gw_id": result.gw_id,
                "local_key": result.local_key,
                "ip_address": result.ip_address,
                "product_key": result.product_key,
                "sw_ver": result.sw_ver,
            }
        )

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        """Server-Sent Events stream for real-time activation notifications.

        The browser connects here and receives an ``activated`` event when the
        device calls the fake-cloud endpoint.

        Args:
            request: Incoming HTTP request from the browser.

        Returns:
            SSE stream response (kept open until client disconnects).
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._sse_queues.append(queue)

        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            }
        )
        await response.prepare(request)

        try:
            # Send initial keep-alive comment
            await response.write(b": connected\n\n")

            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    # Send SSE keep-alive comment (prevents proxy timeouts)
                    await response.write(b": keepalive\n\n")
                    continue

                if message is None:
                    break

                await response.write(message.encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if queue in self._sse_queues:
                self._sse_queues.remove(queue)

        return response

    # ── Private helpers ────────────────────────────────────────────────────

    async def _broadcast_sse(self, event: str, data: str) -> None:
        """Push a Server-Sent Event to all connected browsers.

        Args:
            event: SSE event name (e.g. ``"activated"``).
            data:  JSON string payload.
        """
        message = f"event: {event}\ndata: {data}\n\n"
        for q in list(self._sse_queues):
            await q.put(message)

    def _expire_old_results(self) -> None:
        """Remove activation results older than :const:`_RESULT_TTL_SECS`."""
        now = time.monotonic()
        expired = [
            token
            for token, result in self._results.items()
            if now - result.timestamp > _RESULT_TTL_SECS
        ]
        for token in expired:
            del self._results[token]


# ── Module-level singleton helpers ────────────────────────────────────────────

#: Domain data key for the running PairingServer instance
_KEY_PAIRING_SERVER = "pairing_server"


def get_pairing_server(hass: HomeAssistant) -> PairingServer | None:
    """Return the running :class:`PairingServer` for this HA instance, or ``None``.

    Args:
        hass: Home Assistant instance.

    Returns:
        Running server or ``None`` if not yet started.
    """
    from .const import DOMAIN

    return hass.data.get(DOMAIN, {}).get(_KEY_PAIRING_SERVER)


async def ensure_pairing_server(hass: HomeAssistant) -> PairingServer:
    """Start the pairing server if not already running and return it.

    Idempotent — safe to call from every ``async_setup_entry``.

    Args:
        hass: Home Assistant instance.

    Returns:
        Running :class:`PairingServer` instance.
    """
    from .const import DOMAIN

    domain_data: dict[str, object] = hass.data.setdefault(DOMAIN, {})
    server = domain_data.get(_KEY_PAIRING_SERVER)

    if not isinstance(server, PairingServer):
        server = PairingServer(hass)
        await server.start()
        domain_data[_KEY_PAIRING_SERVER] = server

    return server


async def stop_pairing_server(hass: HomeAssistant) -> None:
    """Stop the pairing server if running.

    Should be called when the last Tuya Cloudless config entry is unloaded.

    Args:
        hass: Home Assistant instance.
    """
    from .const import DOMAIN

    raw = hass.data.get(DOMAIN)
    if not isinstance(raw, dict):
        return

    server = raw.pop(_KEY_PAIRING_SERVER, None)
    if isinstance(server, PairingServer):
        await server.stop()
