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
import re
import secrets
import shutil
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

#: Maximum seconds an activation result is kept in memory (1 hour — covers
#: users who step away briefly after pairing and return to complete HA setup)
_RESULT_TTL_SECS: Final[float] = 3600.0

#: Path to the static web UI assets (relative to this file)
_UI_DIR: Final[Path] = Path(__file__).parent / "pairing_ui"

#: Path to the brand assets directory (icon.png etc.)
_BRAND_DIR: Final[Path] = Path(__file__).parent / "brand"

#: URL prefix for pairing views on HA's own HTTP/HTTPS server
_HA_PAIRING_PREFIX: Final[str] = "/api/tuya_cloudless/pairing"

#: local_key length in bytes (Tuya standard: 16 bytes → 16 ASCII chars)
_LOCAL_KEY_BYTES: Final[int] = 16

#: Tuya device AP gateway IP — devices in AP mode always use this address
_TUYA_AP_GATEWAY_IP: Final[str] = "192.168.4.1"

#: Timeout (seconds) for the HTTP POST to the Tuya device gateway
_TUYA_AP_GW_TIMEOUT: Final[float] = 8.0

#: Timeout (seconds) for waiting for WiFi reconnect after AP pair
_TUYA_AP_RECONNECT_TIMEOUT: Final[float] = 20.0

# ── Rate limiting constants ─────────────────────────────────────────────────

#: Maximum activation requests per IP per window (SEC-001 / PLAT-824)
_RATE_LIMIT_MAX: Final[int] = 10

#: Rate-limit sliding window in seconds (SEC-001 / PLAT-824)
_RATE_LIMIT_WINDOW: Final[float] = 60.0

# ── SSE connection constants ────────────────────────────────────────────────

#: Maximum concurrent SSE connections (SEC-002 / PLAT-825)
_MAX_SSE_CONNECTIONS: Final[int] = 10

# ── WiFi AP discovery constants ────────────────────────────────────────────────

#: Compiled pattern matching ASCII control characters (0x00-0x1F, 0x7F).
#: These are invalid in WiFi SSIDs/passwords and could interfere with
#: nmcli argument handling even when using list-based subprocess calls.
_CTRL_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")

#: SSID prefixes used by Tuya devices in AP/provisioning mode.
#: Matching is case-insensitive.
_TUYA_AP_PREFIXES: Final[tuple[str, ...]] = (
    "smartlife_",
    "sl_",
    "az_",
    "tuya_",
    "wifi_",
)


def _is_tuya_ap(ssid: str) -> bool:
    """Return True if *ssid* looks like a Tuya AP provisioning network.

    Args:
        ssid: WiFi SSID string to check.

    Returns:
        ``True`` when the SSID matches any :const:`_TUYA_AP_PREFIXES` entry
        (case-insensitive), ``False`` otherwise.
    """
    lower = ssid.strip().lower()
    return any(lower.startswith(p) for p in _TUYA_AP_PREFIXES)


# ── Security constants ─────────────────────────────────────────────────────

#: Security headers added to every response from the pairing UI (PLAT-725).
#: These protect the browser-side pairing page against common web attacks.
_SECURITY_HEADERS: Final[dict[str, str]] = {
    # Prevent the pairing page from being embedded in an iframe on another origin
    "X-Frame-Options": "SAMEORIGIN",
    # Stop browsers guessing content types (helps mitigate XSS via MIME sniffing)
    "X-Content-Type-Options": "nosniff",
    # Restrict Referrer header to same-origin only
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Basic CSP — allow scripts/styles only from same origin, no inline eval
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "frame-ancestors 'self';"
    ),
}


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
        # Config flow IDs waiting for device activation
        self._pending_flows: set[str] = set()
        # Auto-stop task (stops server 60s after last flow unregisters)
        self._auto_stop_task: asyncio.Task[None] | None = None
        # Per-IP rate limit: maps IP → list of request timestamps (SEC-001)
        self._rate_limit: dict[str, list[float]] = {}
        # Guard: only register HA views once per server instance
        self._ha_views_registered: bool = False
        # Strong references to background tasks so GC doesn't cancel them (RUF006)
        self._background_tasks: set[asyncio.Task[None]] = set()
        # APs currently being paired — prevents duplicate concurrent pairing tasks
        self._wifi_ap_pairing_in_progress: set[str] = set()

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
        self._site = web.TCPSite(self._runner, "0.0.0.0", self._port)  # nosec B104 — pairing server must bind on all interfaces to be reachable from LAN
        await self._site.start()
        _LOGGER.info("Tuya Cloudless pairing server listening on port %d", self._port)
        await register_redirect_view(self._hass, self._port)
        await self._register_ha_views()

    async def stop(self) -> None:
        """Stop the HTTP server and clean up resources."""
        # Signal all SSE subscribers to close
        for q in list(self._sse_queues):
            await q.put(None)
        self._sse_queues.clear()

        # Discard pending flow IDs — they belong to the current session and
        # must not carry over if the server is restarted (e.g. new provisioning).
        self._pending_flows.clear()
        self._results.clear()

        if self._auto_stop_task is not None:
            self._auto_stop_task.cancel()
            self._auto_stop_task = None

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

        Uses HA's network helper to resolve the actual local address, with
        a sequence of fallbacks so the link works in every install type.

        Returns:
            Absolute ``http://`` URL string for the pairing server.
        """
        from urllib.parse import urlparse

        # 1. Try HA's network helper (respects internal_url + mDNS hostname)
        try:
            from homeassistant.helpers.network import get_url

            base = get_url(self._hass, allow_internal=True, allow_external=False)
            parsed = urlparse(base)
            host = parsed.hostname or ""
            if host:
                return f"http://{host}:{self._port}"
        except Exception as exc:  # broad catch intentional — URL resolution must never crash
            _LOGGER.debug("HA network helper unavailable, trying fallback: %s", exc)

        # 2. Fall back to hass.config.internal_url
        try:
            internal = getattr(self._hass.config, "internal_url", None)
            if isinstance(internal, str) and internal:
                parsed = urlparse(internal)
                host = parsed.hostname or ""
                if host:
                    return f"http://{host}:{self._port}"
        except Exception as exc:  # broad catch intentional — URL resolution must never crash
            _LOGGER.debug("internal_url fallback failed: %s", exc)

        # 3. Use the machine's actual hostname as last resort
        import socket

        hostname = socket.getfqdn() or socket.gethostname() or "homeassistant.local"
        return f"http://{hostname}:{self._port}"

    def ha_ui_url(self) -> str:
        """Return the URL to open the pairing UI.

        When HA is configured for HTTPS, returns the HA-relative HTTPS path so
        the browser runs in a secure context and Web Bluetooth is available.
        Tries internal first, then external (Nabu Casa / reverse proxy).
        Falls back to the HTTP port-8099 URL when no HTTPS URL is available.

        Returns:
            Absolute URL string for the pairing UI (HTTPS when possible).
        """
        try:
            from homeassistant.helpers.network import NoURLAvailableError, get_url

            for kwargs in (
                {"allow_internal": True, "allow_external": False},
                {"allow_internal": False, "allow_external": True},
            ):
                try:
                    base = get_url(self._hass, **kwargs)
                    if base.startswith("https://"):
                        return base.rstrip("/") + _HA_PAIRING_PREFIX
                except NoURLAvailableError:
                    continue
        except Exception as exc:  # broad catch intentional — URL resolution must never crash
            _LOGGER.debug("ha_ui_url HTTPS fallback: %s", exc)
        return self.ha_local_url()

    def register_flow(self, flow_id: str) -> None:
        """Register a config flow to be notified when a device activates.

        Cancels any pending idle-stop timer.

        Args:
            flow_id: HA config flow ID waiting for activation.
        """
        self._pending_flows.add(flow_id)
        if self._auto_stop_task is not None:
            self._auto_stop_task.cancel()
            self._auto_stop_task = None

    def unregister_flow(self, flow_id: str) -> None:
        """Unregister a config flow (e.g. when the flow is aborted or completed).

        Schedules an idle stop 60 s after the last flow unregisters.

        Args:
            flow_id: HA config flow ID to remove.
        """
        self._pending_flows.discard(flow_id)
        if not self._pending_flows and self._auto_stop_task is None:
            self._auto_stop_task = asyncio.create_task(
                self._auto_stop_after_idle(), name="tuya-cloudless-auto-stop"
            )

    # ── Route handlers ─────────────────────────────────────────────────────

    def _build_app(self) -> web.Application:
        """Build and return the aiohttp Application with all routes registered."""
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/provision/config", self._handle_config)
        app.router.add_get("/api/provision/qr.svg", self._handle_qr)
        app.router.add_get("/api/provision/events", self._handle_sse)
        app.router.add_get("/api/provision/result/{token}", self._handle_get_result)
        app.router.add_post("/api/tuya/device/active", self._handle_activate)
        # Also accept the Tuya cloud API path format some firmware uses
        app.router.add_post("/api.json", self._handle_activate)
        app.router.add_get("/api/provision/wifi-scan", self._handle_wifi_scan)
        app.router.add_get("/api/provision/quick-scan", self._handle_quick_scan)
        app.router.add_post("/api/provision/wifi-ap-pair", self._handle_wifi_ap_pair)
        # Serve brand icon at /static/icon.png (before the catch-all static mount)
        app.router.add_get("/static/icon.png", self._handle_icon)
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
                headers=_SECURITY_HEADERS,
            )
        return web.Response(
            body=index_path.read_bytes(),
            content_type="text/html",
            charset="utf-8",
            headers=_SECURITY_HEADERS,
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
                     "events_url": "...", "result_url_template": "...",
                     "default_ssid": "<active SSID or null>"}``
        """
        base = self.ha_local_url()
        default_ssid = await self._get_default_ssid()
        return web.json_response(
            {
                "activator_url": base,
                "events_url": f"{base}/api/provision/events",
                "result_url_template": f"{base}/api/provision/result/{{token}}",
                "default_ssid": default_ssid,
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_icon(self, request: web.Request) -> web.Response:
        """Serve the brand icon PNG at ``/static/icon.png``.

        Reads ``brand/icon.png`` adjacent to this file.  Returns 404 if the
        brand directory or icon file does not exist.

        Args:
            request: Incoming HTTP request.

        Returns:
            PNG image response or 404.
        """
        icon_path = _BRAND_DIR / "icon.png"
        if not icon_path.is_file():
            return web.Response(status=404, text="Icon not found")
        return web.Response(
            body=icon_path.read_bytes(),
            content_type="image/png",
            headers={"Cache-Control": "max-age=86400"},
        )

    async def _handle_qr(self, request: web.Request) -> web.Response:
        """Serve a QR code SVG encoding the pairing server LAN URL.

        Useful for mobile users who want to open the pairing page on an Android
        device with Chrome (which supports Web Bluetooth).  Requires the
        optional ``qrcode[svg]`` package — returns 503 if not installed.

        Args:
            request: Incoming HTTP request.

        Returns:
            SVG image response encoding ``ha_local_url()``, or 503 if the
            ``qrcode`` library is not installed.
        """
        # Rate limit: QR generation is CPU-bound — 20/min per IP
        client_ip = request.remote or "unknown"
        if self._is_rate_limited(client_ip, max_requests=20, window=60.0):
            return web.Response(
                status=429,
                text="Too Many Requests",
                headers={"Retry-After": "3"},
            )

        try:
            import io

            import qrcode  # type: ignore[import-untyped]
            import qrcode.image.svg  # type: ignore[import-untyped]
        except ImportError:
            return web.Response(
                status=503,
                text=(
                    "QR code requires the qrcode library.\n"
                    "Install with: pip install 'tuya-cloudless[qr]'"
                ),
            )

        url = self.ha_local_url()
        factory = qrcode.image.svg.SvgPathImage
        qr_img = qrcode.make(  # type: ignore[call-overload]
            url, image_factory=factory, box_size=10, border=2
        )
        buf = io.BytesIO()
        qr_img.save(buf)
        svg_data = buf.getvalue()
        return web.Response(
            body=svg_data,
            content_type="image/svg+xml",
            headers={
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*",
            },
        )

    async def _handle_activate(self, request: web.Request) -> web.Response:
        """Handle the Tuya device activation POST.

        The device calls this endpoint after connecting to WiFi.  The request
        body (JSON) contains the device ID, product key, token, and firmware
        version.  We generate a random ``local_key`` and respond in Tuya cloud
        format so the device accepts it.

        CSRF protection (PLAT-725): We reject requests that do not carry a
        ``Content-Type: application/json`` header.  A browser-initiated CSRF
        attack via an HTML ``<form>`` can only submit
        ``application/x-www-form-urlencoded`` or ``multipart/form-data``.
        Requiring JSON prevents such simple-form attacks while all real Tuya
        firmware sends JSON payloads.

        Args:
            request: Incoming HTTP request from the Tuya device.

        Returns:
            JSON response in Tuya cloud activation format.
        """
        # Reject requests that don't claim to be JSON — blocks simple-form CSRF.
        # Real Tuya firmware always sends Content-Type: application/json.
        content_type = request.content_type or ""
        if not content_type.startswith("application/json"):
            _LOGGER.warning(
                "Activate endpoint rejected request with Content-Type: %s (CSRF guard)",
                content_type or "<none>",
            )
            return web.Response(status=415, text="Content-Type must be application/json")

        client_ip = request.remote or "unknown"

        # Per-IP rate limiting (SEC-001 / PLAT-824)
        if self._is_rate_limited(client_ip):
            return web.Response(status=429, text="Too Many Requests")

        _LOGGER.debug("Activation request from %s", client_ip)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            body = {}

        # Support both top-level and nested ``data`` field
        data: dict[str, object] = body.get("data", body)  # type: ignore[assignment]

        gw_id = str(data.get("gw_id") or data.get("gwId") or "")
        product_key = str(data.get("product_key") or data.get("productKey") or "")
        token = str(data.get("token") or "")
        sw_ver = str(data.get("sw_ver") or data.get("swVer") or "")

        # Sanitise field lengths — reject absurdly long values that could stress
        # SSE subscribers or the frontend renderer (PLAT-825 / SEC-002).
        if len(gw_id) > 64 or len(product_key) > 64 or len(token) > 128 or len(sw_ver) > 32:
            _LOGGER.warning(
                "Activation request rejected: field too long "
                "(gw_id=%d product_key=%d token=%d sw_ver=%d)",
                len(gw_id),
                len(product_key),
                len(token),
                len(sw_ver),
            )
            return web.Response(status=400, text="Field too long")

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

        # Resume any waiting HA config flows
        flow_data = {
            "gw_id": gw_id,
            "local_key": local_key,
            "ip_address": client_ip,
            "product_key": product_key,
        }
        for flow_id in list(self._pending_flows):
            self._hass.async_create_task(
                self._hass.config_entries.flow.async_configure(flow_id, flow_data)
            )

        # Respond in Tuya cloud activation format
        response_body = {
            "t": int(time.time()),
            "success": True,
            "result": {
                "gwId": gw_id,
                "active": 2,
                "ability": 0,
                "localKey": local_key,
                "timezone": (
                    tz
                    if isinstance((tz := getattr(self._hass.config, "time_zone", None)), str) and tz
                    else "UTC"
                ),
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
        # Enforce connection cap before allocating resources (SEC-002 / PLAT-825)
        if len(self._sse_queues) >= _MAX_SSE_CONNECTIONS:
            return web.Response(
                status=503,
                text="Too many concurrent SSE connections",
                headers={"Retry-After": "10"},
            )

        # Bounded queue prevents unbounded memory growth (SEC-002 / PLAT-825)
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=32)
        self._sse_queues.append(queue)

        # No wildcard CORS on SSE — only same-origin browser pages need this
        # (SEC-003 / PLAT-826)
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
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

    async def _handle_wifi_scan(self, request: web.Request) -> web.Response:
        """Return nearby WiFi SSIDs via nmcli. Returns empty list if unavailable.

        Two-pass scan for better coverage:
          1. ``--rescan yes`` — triggers a real OTA scan and waits for it to
             finish (typically 5-10 s).
          2. Wait 2 s, then ``--rescan no`` — re-reads the now-populated cache
             to capture any networks that appeared during the driver's scan
             window but were not yet ready when pass 1 exited.

        Merges both pass results into a de-duplicated list sorted
        alphabetically.  Also returns ``current_ssid`` (the SSID of the
        currently active WiFi connection on the HA host) so the UI can
        highlight it at the top of the dropdown.

        Args:
            request: Incoming HTTP request.

        Returns:
            JSON: ``{"ssids": [...], "current_ssid": str | null}``
        """
        # Rate limit: wifi-scan is expensive (triggers OTA scan) — 5/min per IP
        client_ip = request.remote or "unknown"
        if self._is_rate_limited(client_ip, max_requests=5, window=60.0):
            return web.Response(
                status=429,
                text="Too Many Requests",
                headers={"Retry-After": "15"},
            )

        seen: set[str] = set()
        ssids: list[str] = []
        current_ssid: str | None = await self._get_default_ssid()

        async def _run_nmcli(rescan: str) -> list[str]:
            proc = await asyncio.create_subprocess_exec(
                "nmcli",
                "--terse",
                "--fields",
                "SSID",
                "device",
                "wifi",
                "list",
                "--rescan",
                rescan,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            result: list[str] = []
            for line in stdout.decode(errors="replace").splitlines():
                ssid = line.strip()
                if ssid and ssid != "--":
                    result.append(ssid)
            return result

        try:
            # Pass 1: trigger real scan
            for ssid in await _run_nmcli("yes"):
                if ssid not in seen:
                    seen.add(ssid)
                    ssids.append(ssid)

            # Brief pause so the driver can post-process the scan results
            await asyncio.sleep(2)

            # Pass 2: re-read cache — picks up networks missed in pass 1
            for ssid in await _run_nmcli("no"):
                if ssid not in seen:
                    seen.add(ssid)
                    ssids.append(ssid)

        except (FileNotFoundError, TimeoutError, OSError) as exc:
            _LOGGER.debug("WiFi scan unavailable: %s", exc)

        # Sort alphabetically; if current SSID is known, bubble it to the top
        ssids.sort(key=str.casefold)
        if current_ssid and current_ssid in seen:
            ssids.remove(current_ssid)
            ssids.insert(0, current_ssid)

        tuya_aps = [{"ssid": s} for s in ssids if _is_tuya_ap(s)]

        return web.json_response(
            {"ssids": ssids, "current_ssid": current_ssid, "tuya_aps": tuya_aps},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_quick_scan(self, request: web.Request) -> web.Response:
        """Fast cached nmcli read — no OTA scan triggered.

        Reads the OS WiFi cache without ``--rescan yes``, so it completes in
        well under one second.  Filters results to Tuya AP prefixes only.

        This endpoint is called automatically on page load (no user gesture
        required) to populate the device discovery panel.

        Args:
            request: Incoming HTTP request.

        Returns:
            JSON: ``{"tuya_aps": [{"ssid": "SmartLife_AB12"}, ...]}``
        """
        # Rate limit: quick-scan is cheap but still reveals nearby network names — 10/min per IP
        client_ip = request.remote or "unknown"
        if self._is_rate_limited(client_ip, max_requests=10, window=60.0):
            return web.Response(
                status=429,
                text="Too Many Requests",
                headers={"Retry-After": "6"},
            )

        tuya_aps: list[dict[str, str]] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli",
                "--terse",
                "--fields",
                "SSID",
                "device",
                "wifi",
                "list",
                "--rescan",
                "no",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            seen: set[str] = set()
            for line in stdout.decode(errors="replace").splitlines():
                ssid = line.strip()
                if ssid and ssid != "--" and ssid not in seen and _is_tuya_ap(ssid):
                    seen.add(ssid)
                    tuya_aps.append({"ssid": ssid})
        except (FileNotFoundError, TimeoutError, OSError) as exc:
            _LOGGER.debug("Quick WiFi scan unavailable: %s", exc)

        return web.json_response(
            {"tuya_aps": tuya_aps},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_wifi_ap_pair(self, request: web.Request) -> web.Response:
        """Initiate WiFi-AP provisioning for a Tuya device in AP mode.

        Connects the HA host to the Tuya device's AP, sends credentials, then
        reconnects to the home WiFi.  The device joins home WiFi and POSTs to
        the fake-cloud endpoint, triggering the standard SSE activation flow.

        Body JSON:
            ``{"ap_ssid": "SmartLife_AB12", "home_ssid": "HomeNet", "home_password": "…"}``

        Args:
            request: Incoming HTTP request from the browser.

        Returns:
            JSON: ``{"token": "...", "events_url": "/api/provision/events"}``
            Returns immediately; pairing happens in the background.
        """
        try:
            body: dict[str, object] = await request.json()
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return web.Response(status=400, text="Invalid JSON body")

        ap_ssid = str(body.get("ap_ssid") or "").strip()
        home_ssid = str(body.get("home_ssid") or "").strip()
        home_password = str(body.get("home_password") or "")

        if not ap_ssid or not home_ssid:
            return web.Response(status=400, text="ap_ssid and home_ssid are required")

        # Enforce WiFi spec limits: SSID ≤ 32 bytes, WPA2 password ≤ 63 bytes
        if len(ap_ssid.encode()) > 32 or len(home_ssid.encode()) > 32:
            return web.Response(status=400, text="SSID exceeds 32-byte WiFi limit")
        if len(home_password.encode()) > 63:
            return web.Response(status=400, text="Password exceeds 63-byte WPA2 limit")

        # Reject control characters (0x00-0x1F, 0x7F) in any field
        if (
            _CTRL_CHAR_RE.search(ap_ssid)
            or _CTRL_CHAR_RE.search(home_ssid)
            or _CTRL_CHAR_RE.search(home_password)
        ):
            return web.Response(
                status=400, text="SSID/password must not contain control characters"
            )

        # Prevent duplicate concurrent pairing tasks for the same AP
        if ap_ssid in self._wifi_ap_pairing_in_progress:
            return web.Response(
                status=409, text="WiFi AP pairing already in progress for this device"
            )

        # Fail fast if nmcli is unavailable — avoids silent 120s timeout for user
        if shutil.which("nmcli") is None:
            return web.Response(
                status=503,
                text="nmcli not available; WiFi AP pairing requires NetworkManager",
            )

        token = secrets.token_hex(_LOCAL_KEY_BYTES)
        activator_url = self.ha_local_url()

        self._wifi_ap_pairing_in_progress.add(ap_ssid)
        task = asyncio.create_task(
            self._wifi_ap_pair_task(ap_ssid, home_ssid, home_password, token, activator_url),
            name="tuya-cloudless-wifi-ap-pair",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(lambda _: self._wifi_ap_pairing_in_progress.discard(ap_ssid))

        return web.json_response(
            {"token": token, "events_url": "/api/provision/events"},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _wifi_ap_pair_task(
        self,
        ap_ssid: str,
        home_ssid: str,
        home_pwd: str,
        token: str,
        activator_url: str,
    ) -> None:
        """Background task: connect to Tuya AP, push credentials, reconnect home WiFi.

        Steps:
            1. Record the current connection ID (for restore).
            2. Connect to ``ap_ssid`` (open network — Tuya APs have no password).
            3. POST credentials + token + activator URL to ``http://192.168.4.1/gw.json``.
               The POST may appear to fail because the device switches WiFi — that is OK.
            4. Reconnect to the previous home connection.
            5. Device joins home WiFi → POSTs to fake-cloud → SSE "activated" fires.

        On failure the error is broadcast via SSE as a ``wifi_ap_error`` event.

        Args:
            ap_ssid:       SSID of the Tuya AP (e.g. ``SmartLife_AB12``).
            home_ssid:     SSID of the home WiFi network.
            home_pwd:      Password for the home WiFi network.
            token:         Provisioning token (stored in :attr:`_results` on activation).
            activator_url: HTTP URL of the fake-cloud endpoint on the HA host.
        """
        prev_connection: str | None = None

        async def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
            """Run a shell command and return (returncode, stdout)."""
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                return -1, ""
            return proc.returncode or 0, stdout.decode(errors="replace").strip()

        try:
            # Step 1: find current active connection name (for restore later)
            rc, out = await _run(
                ["nmcli", "--terse", "--fields", "ACTIVE,NAME", "connection", "show"]
            )
            if rc == 0:
                for line in out.splitlines():
                    if line.startswith("yes:"):
                        prev_connection = line[4:].strip() or None
                        break

            # Step 2: connect to the Tuya AP (open network)
            _LOGGER.info("WiFi AP pair: connecting to %s", ap_ssid)
            rc, _ = await _run(
                ["nmcli", "device", "wifi", "connect", ap_ssid],
                timeout=15.0,  # Tuya APs are open; 15s is generous
            )
            if rc != 0:
                raise OSError(f"nmcli connect to {ap_ssid!r} failed (rc={rc})")

            # Short wait for IP assignment on the Tuya AP (192.168.4.x)
            await asyncio.sleep(2)

            # Step 3: POST credentials to the Tuya device
            # Import here to avoid requiring aiohttp at module level (it's always available
            # in HA, but tests can mock it).
            import aiohttp as _aiohttp

            payload = {
                "s": home_ssid,
                "p": home_pwd,
                "t": token,
                "r": "az",
                "activator": activator_url,
            }
            _LOGGER.info("WiFi AP pair: sending credentials to device gateway")
            try:
                async with (
                    _aiohttp.ClientSession() as session,
                    session.post(
                        f"http://{_TUYA_AP_GATEWAY_IP}/gw.json",
                        json=payload,
                        timeout=_aiohttp.ClientTimeout(total=_TUYA_AP_GW_TIMEOUT),
                    ) as resp,
                ):
                    _LOGGER.debug("gw.json response: %s", resp.status)
            except Exception as exc:
                # Expected — device switches WiFi mid-request; log and continue
                _LOGGER.debug("gw.json POST ended early (device switching WiFi): %s", exc)

        except (FileNotFoundError, OSError) as exc:
            _LOGGER.warning("WiFi AP pair task failed: %s", exc)
            await self._broadcast_sse(
                "wifi_ap_error",
                json.dumps({"error": "WiFi control unavailable or connect failed", "token": token}),
            )
        except Exception as exc:
            _LOGGER.exception("Unexpected error in WiFi AP pair task: %s", exc)
            await self._broadcast_sse(
                "wifi_ap_error",
                json.dumps({"error": "Unexpected pairing error", "token": token}),
            )
        finally:
            # Step 4: always try to restore connectivity
            if prev_connection:
                _LOGGER.info("WiFi AP pair: reconnecting to %s", prev_connection)
                try:
                    await _run(
                        ["nmcli", "connection", "up", prev_connection],
                        timeout=_TUYA_AP_RECONNECT_TIMEOUT,
                    )
                except Exception as exc:
                    _LOGGER.warning("WiFi AP pair: reconnect failed: %s", exc)
            else:
                # No saved connection — reconnect to home WiFi directly so we
                # don't remain stuck on the Tuya AP after provisioning.
                _LOGGER.info(
                    "WiFi AP pair: no saved connection; reconnecting to home WiFi (%s)",
                    home_ssid,
                )
                try:
                    cmd: list[str] = [
                        "nmcli",
                        "device",
                        "wifi",
                        "connect",
                        home_ssid,
                    ]
                    if home_pwd:
                        cmd += ["password", home_pwd]
                    await _run(cmd, timeout=_TUYA_AP_RECONNECT_TIMEOUT)
                except Exception as exc:
                    _LOGGER.warning("WiFi AP pair: home WiFi reconnect failed: %s", exc)

    # ── HA HTTPS views ─────────────────────────────────────────────────────

    async def _handle_ha_index(self, request: web.Request) -> web.Response:
        """Serve index.html via HA's HTTPS server with paths and globals injected.

        Rewrites all ``/static/`` references to the HA-relative path and injects
        JS globals so ``app.js`` uses the correct provision base URL and the HTTP
        port-8099 URL for the Tuya device activator.

        Args:
            request: Incoming HTTP request.

        Returns:
            HTML response with rewritten paths and injected globals.
        """
        index_path = _UI_DIR / "index.html"
        if not index_path.is_file():
            return web.Response(
                status=404,
                text="Pairing UI not found. This is a bug — please report it.",
            )

        html = index_path.read_text(encoding="utf-8")
        static_base = _HA_PAIRING_PREFIX + "/static"
        provision_base = _HA_PAIRING_PREFIX + "/provision"
        activator_url = self.ha_local_url()  # Always HTTP — Tuya device cannot do TLS

        # Rewrite /static/ asset references to the HA-relative path
        html = html.replace('="/static/', f'="{static_base}/')
        # Rewrite QR image src to HA path (404 → JS error handler shows "unavailable")
        html = html.replace(
            'src="/api/provision/qr.svg"',
            f'src="{provision_base}/qr.svg"',
        )

        # Inject window globals before </head> so app.js reads them on load
        globals_script = (
            "<script>\n"
            f"window._TUYA_ACTIVATOR_BASE={json.dumps(activator_url)};\n"
            f"window._TUYA_PROVISION_BASE={json.dumps(provision_base)};\n"
            f"window._TUYA_STATIC_BASE={json.dumps(static_base)};\n"
            "</script>\n"
        )
        html = html.replace("</head>", globals_script + "</head>", 1)

        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def _handle_ha_config(self, request: web.Request) -> web.Response:
        """Config endpoint when the pairing UI is served via HA HTTPS.

        Returns the HTTP port-8099 URL as ``activator_url`` (Tuya device cannot
        do TLS) but HA-relative paths for ``events_url`` and
        ``result_url_template`` so the browser uses same-origin HTTPS.

        Args:
            request: Incoming HTTP request from the browser.

        Returns:
            JSON configuration for the pairing UI.
        """
        activator_url = self.ha_local_url()
        provision_base = _HA_PAIRING_PREFIX + "/provision"
        default_ssid = await self._get_default_ssid()
        return web.json_response(
            {
                "activator_url": activator_url,
                "events_url": f"{provision_base}/events",
                "result_url_template": f"{provision_base}/result/{{token}}",
                "default_ssid": default_ssid,
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _register_ha_views(self) -> None:
        """Register pairing UI views on HA's HTTP server (HTTPS when HA is HTTPS).

        This mirrors the port-8099 endpoints on HA's own server so the browser
        runs in a secure context and Web Bluetooth works.  The Tuya device
        activation endpoint stays on port 8099 (HTTP) because devices cannot
        do TLS.

        Safe to call multiple times — guarded by ``_ha_views_registered``.
        """
        if self._ha_views_registered:
            return
        self._ha_views_registered = True

        import mimetypes

        from homeassistant.components.http import HomeAssistantView

        server = self
        ui_dir = _UI_DIR

        class _PairingIndexView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/"
            name = "api:tuya_cloudless:pairing:index"

            async def get(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_ha_index(request)

        class _PairingConfigView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/provision/config"
            name = "api:tuya_cloudless:pairing:config"

            async def get(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_ha_config(request)

        class _PairingEventsView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/provision/events"
            name = "api:tuya_cloudless:pairing:events"

            async def get(  # type: ignore[override]
                self, request: web.Request
            ) -> web.StreamResponse:
                return await server._handle_sse(request)

        class _PairingResultView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/provision/result/{token}"
            name = "api:tuya_cloudless:pairing:result"

            async def get(  # type: ignore[override]
                self, request: web.Request, token: str
            ) -> web.Response:
                return await server._handle_get_result(request)

        class _PairingWifiScanView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/provision/wifi-scan"
            name = "api:tuya_cloudless:pairing:wifi_scan"

            async def get(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_wifi_scan(request)

        class _PairingQuickScanView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/provision/quick-scan"
            name = "api:tuya_cloudless:pairing:quick_scan"

            async def get(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_quick_scan(request)

        class _PairingWifiApPairView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/provision/wifi-ap-pair"
            name = "api:tuya_cloudless:pairing:wifi_ap_pair"

            async def post(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_wifi_ap_pair(request)

        class _PairingStaticView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/static/{path:.+}"
            name = "api:tuya_cloudless:pairing:static"

            async def get(  # type: ignore[override]
                self, request: web.Request, path: str
            ) -> web.Response:
                file_path = (ui_dir / path).resolve()
                # Security: prevent path traversal outside the UI directory
                try:
                    ui_resolved = ui_dir.resolve()
                    brand_resolved = _BRAND_DIR.resolve()
                    if not str(file_path).startswith(str(ui_resolved)):
                        return web.Response(status=403, text="Forbidden")
                except (ValueError, OSError):
                    return web.Response(status=400, text="Bad request")
                if not file_path.is_file():
                    # Fallback: serve brand assets (icon.png, logo.png, etc.)
                    brand_path = (_BRAND_DIR / path).resolve()
                    if str(brand_path).startswith(str(brand_resolved)) and brand_path.is_file():
                        file_path = brand_path
                    else:
                        return web.Response(status=404, text="Not found")
                content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                return web.Response(
                    body=file_path.read_bytes(),
                    content_type=content_type,
                    headers={"Cache-Control": "max-age=3600"},
                )

        self._hass.http.register_view(_PairingIndexView())
        self._hass.http.register_view(_PairingConfigView())
        self._hass.http.register_view(_PairingEventsView())
        self._hass.http.register_view(_PairingResultView())
        self._hass.http.register_view(_PairingWifiScanView())
        self._hass.http.register_view(_PairingQuickScanView())
        self._hass.http.register_view(_PairingWifiApPairView())
        self._hass.http.register_view(_PairingStaticView())

    # ── Private helpers ────────────────────────────────────────────────────

    async def _get_default_ssid(self) -> str | None:
        """Return the currently active WiFi SSID on the HA host, or ``None``.

        Runs ``nmcli --terse --fields ACTIVE,SSID device wifi list`` and
        returns the SSID of the active connection (the line starting with
        ``yes:``).  Returns ``None`` if nmcli is unavailable or no active
        WiFi connection is found.

        Returns:
            SSID string, or ``None`` if unavailable.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli",
                "--terse",
                "--fields",
                "ACTIVE,SSID",
                "device",
                "wifi",
                "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            for line in stdout.decode(errors="replace").splitlines():
                if line.startswith("yes:"):
                    ssid = line[4:].strip()
                    if ssid and ssid != "--":
                        return ssid
        except (FileNotFoundError, TimeoutError, OSError) as exc:
            _LOGGER.debug("Default SSID lookup unavailable: %s", exc)
        return None

    def _is_rate_limited(
        self,
        client_ip: str,
        max_requests: int = _RATE_LIMIT_MAX,
        window: float = _RATE_LIMIT_WINDOW,
    ) -> bool:
        """Return True and log if ``client_ip`` has exceeded the rate limit.

        Maintains the sliding-window bucket in :attr:`_rate_limit` and evicts
        stale entries to prevent unbounded dict growth.

        Args:
            client_ip:    Remote IP address string.
            max_requests: Max allowed requests in the window (default from constant).
            window:       Sliding window in seconds (default from constant).
        """
        now = time.monotonic()
        timestamps = self._rate_limit.get(client_ip, [])
        timestamps = [ts for ts in timestamps if now - ts < window]
        if len(timestamps) >= max_requests:
            _LOGGER.warning(
                "Rate limit exceeded: ip=%s requests=%d",
                client_ip,
                len(timestamps),
            )
            return True
        timestamps.append(now)
        self._rate_limit[client_ip] = timestamps
        self._rate_limit = {ip: ts_list for ip, ts_list in self._rate_limit.items() if ts_list}
        return False

    async def _broadcast_sse(self, event: str, data: str) -> None:
        """Push a Server-Sent Event to all connected browsers.

        Uses a 1-second timeout per queue so a stalled client cannot block the
        entire pairing flow.  Queues that time out are removed as stale.

        Args:
            event: SSE event name (e.g. ``"activated"``).
            data:  JSON string payload.
        """
        message = f"event: {event}\ndata: {data}\n\n"
        stale: list[asyncio.Queue[str | None]] = []
        for q in list(self._sse_queues):
            try:
                await asyncio.wait_for(q.put(message), timeout=1.0)
            except TimeoutError:
                _LOGGER.debug("SSE queue stalled — removing client")
                stale.append(q)
        for q in stale:
            if q in self._sse_queues:
                self._sse_queues.remove(q)

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

    async def _auto_stop_after_idle(self) -> None:
        """Stop the server 60 s after the last flow unregisters, if still idle."""
        await asyncio.sleep(60)
        if self._pending_flows:
            # A new flow registered while we were sleeping — do nothing.
            self._auto_stop_task = None
            return

        # Verify we're still the active server before stopping.
        from .const import DOMAIN

        current = self._hass.data.get(DOMAIN, {}).get(_KEY_PAIRING_SERVER)
        if current is self:
            _LOGGER.info("Tuya Cloudless pairing server idle for 60 s — stopping automatically")
            await stop_pairing_server(self._hass)

        self._auto_stop_task = None


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


class PairingRedirectView:
    """HA HTTP view that redirects to the pairing server using the browser's host.

    Registered at ``/api/tuya_cloudless/pair/{flow_id}``.  Because this path
    lives on HA's own HTTP server, the browser hits it with the *same*
    hostname/IP it used to reach HA.

    When HA runs behind an HTTPS reverse proxy the incoming request carries an
    ``X-Forwarded-Proto: https`` header set by the proxy.  In that case the
    view redirects to the HA-hosted HTTPS pairing UI
    (``/api/tuya_cloudless/pairing/?flow_id=…``) so the browser remains in a
    secure context and Web Bluetooth is available.

    Without HTTPS the view falls back to ``http://{host}:{port}/?flow_id=…``
    (the standalone port-8099 server).
    """

    requires_auth = False
    url = "/api/tuya_cloudless/pair/{flow_id}"
    name = "api:tuya_cloudless:pair"

    def __init__(self, port: int = PAIRING_SERVER_PORT) -> None:
        self._port = port

    async def get(self, request: web.Request, flow_id: str) -> web.Response:
        """Redirect browser to the pairing UI on the correct host/scheme.

        Checks ``X-Forwarded-Proto`` first so reverse-proxy HTTPS setups are
        handled correctly even when ``external_url`` is not configured in HA.
        """
        proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
        if proto == "https":
            # Stay on HA's HTTPS server — the pairing UI is mirrored there.
            # Using a relative redirect keeps the correct hostname automatically.
            target = f"{_HA_PAIRING_PREFIX}/?flow_id={flow_id}"
        else:
            host = request.url.host or "homeassistant.local"
            target = f"http://{host}:{self._port}/?flow_id={flow_id}"
        raise web.HTTPFound(location=target)


async def register_redirect_view(hass: HomeAssistant, port: int = PAIRING_SERVER_PORT) -> None:
    """Register :class:`PairingRedirectView` with HA's HTTP component.

    Safe to call multiple times — HA deduplicates by view name.

    Args:
        hass: Home Assistant instance.
        port: Port the pairing server listens on.
    """
    from homeassistant.components.http import HomeAssistantView

    class _View(PairingRedirectView, HomeAssistantView):
        pass

    hass.http.register_view(_View(port=port))


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
