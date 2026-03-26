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
import contextlib
import ipaddress
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

#: Hard cap on stored activation results to prevent memory exhaustion from
#: repeated activations across multiple source IPs on large LAN segments.
_MAX_STORED_RESULTS: Final[int] = 256

#: Path to the static web UI assets (relative to this file)
_UI_DIR: Final[Path] = Path(__file__).parent / "pairing_ui"

#: Integration version read from manifest.json once at import time.
# R44-F8: Wrapped in try/except — missing/corrupt manifest.json during a
# partial HACS update or development checkout must not crash the import.
try:
    _INTEGRATION_VERSION: Final[str] = json.loads(
        (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
    ).get("version", "unknown")
except (OSError, json.JSONDecodeError):
    _INTEGRATION_VERSION = "unknown"

#: Path to the brand assets directory (icon.png etc.)
_BRAND_DIR: Final[Path] = Path(__file__).parent / "brand"

#: URL prefix for pairing views on HA's own HTTP/HTTPS server
_HA_PAIRING_PREFIX: Final[str] = "/api/tuya_cloudless/pairing"

#: local_key length in characters (Tuya standard: 16 ASCII chars = 16 UTF-8 bytes).
#: Generated as secrets.token_hex(_LOCAL_KEY_BYTES // 2) = 8 random bytes → 16 hex chars.
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

#: Maximum distinct IPs tracked in the rate-limit table.
#: Prevents memory exhaustion from an IP-rotating attacker flooding with many
#: source addresses.  When the cap is reached, the oldest entry is evicted.
_MAX_RATE_LIMIT_IPS: Final[int] = 1024

#: Separate entropy constant for provisioning tokens (distinct from local_key).
#: secrets.token_hex(_PROVISION_TOKEN_BYTES) = 16 bytes = 32 lowercase hex chars.
_PROVISION_TOKEN_BYTES: Final[int] = 16

# ── SSE connection constants ────────────────────────────────────────────────

#: Maximum concurrent SSE connections (SEC-002 / PLAT-825)
_MAX_SSE_CONNECTIONS: Final[int] = 10

#: Maximum concurrent WiFi AP background pairing tasks.
#: Each task runs nmcli and holds a system WiFi interface. A reasonable cap
#: prevents resource exhaustion in case many requests arrive rapidly.
_MAX_WIFI_AP_TASKS: Final[int] = 3

#: Maximum number of SSIDs returned by the wifi-scan endpoint.
#: Limits data exposure — the UI only needs the user's immediate vicinity,
#: not a city-wide survey. Rate-limiting (5/min) provides the primary defence;
#: this cap is a secondary bound on response payload size.
_MAX_SSID_SCAN_RESULTS: Final[int] = 50

#: Maximum size of a static UI file served by _PairingStaticView (5 MiB).
#: Prevents in-memory DoS if a large file is accidentally placed in pairing_ui/.
_MAX_STATIC_FILE_BYTES: Final[int] = 5 * 1024 * 1024

#: Maximum length of an SSE event name.  All internal callers use short literals.
_MAX_SSE_EVENT_LEN: Final[int] = 64
#: Maximum byte length of an SSE data payload (per-event).
_MAX_SSE_DATA_LEN: Final[int] = 4096

# ── WiFi AP discovery constants ────────────────────────────────────────────────

#: Compiled pattern matching ASCII control characters (0x00-0x1F, 0x7F).
#: These are invalid in WiFi SSIDs/passwords and could interfere with
#: nmcli argument handling even when using list-based subprocess calls.
_CTRL_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")

#: R52-F6: Compiled pattern matching ASCII and Unicode line terminators for
#: SSE header-injection defence.  The SSE spec treats LF (U+000A) and CR
#: (U+000D) as line endings; ECMAScript also treats U+0085 (NEL),
#: U+2028 (LINE SEPARATOR) and U+2029 (PARAGRAPH SEPARATOR) as terminators.
#: Guard all of them to prevent injection by future callers.
_SSE_NEWLINE_RE: Final[re.Pattern[str]] = re.compile(r"[\n\r\x85\u2028\u2029]")

#: Allowed characters for Tuya device IDs (gwId, productKey).
#: Alphanumeric + hyphens + underscores — matches all known Tuya ID formats.
#: Excludes control characters and special chars that could inject into SSE/HA config.
_DEVICE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
#: Activation token format — 32 lowercase hex chars (16 random bytes encoded as hex).
#: The BLE JS randomToken() uses crypto.getRandomValues(16 bytes); wifi-ap-pair uses
#: secrets.token_hex(16).  The GET result endpoint enforces this same format.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")
#: HA config flow IDs are UUIDs (hex + hyphens); allow also plain hex without hyphens.
#: Bounded to 128 chars — prevents hash-DoS from very long strings in membership tests.
_FLOW_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")
#: Firmware version allowlist — only printable ASCII chars found in real Tuya versions
#: like "1.0.4" or "2.3.1-beta".  A positive allowlist is safer than the ctrl-char
#: denylist: it also blocks Unicode line terminators (U+2028/U+2029) that bypass
#: [\x00-\x1f\x7f] and could cause issues in JSON contexts or frontend renders.
_SW_VER_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9._\-]{1,32}$")

#: SSID prefixes used by Tuya devices in AP/provisioning mode.
#: Matching is case-insensitive.
_TUYA_AP_PREFIXES: Final[tuple[str, ...]] = (
    # Dash-separated variants (most common in real Tuya devices):
    "smartlife-",  # SmartLife-XXXX  (Tuya app branded, most common)
    "tuya-",  # Tuya-XXXX
    "sl-",  # SL-XXXX  (some OEM variants)
    "az-",  # AZ-XXXX  (some OEM variants)
    # Underscore variants (older / alternate firmware):
    "smartlife_",
    "sl_",
    "az_",
    "tuya_",
    # NOTE: "wifi_" deliberately omitted — it is too broad (matches any SSID starting
    # with "wifi_", e.g. "wifi_evil_hotspot") and would allow an authenticated user to
    # submit that prefix to nmcli connect, connecting HA to an arbitrary AP.
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


def _json_in_script(value: object) -> str:
    """JSON-encode *value* safe for embedding inside an HTML ``<script>`` block.

    ``json.dumps`` alone does not escape ``</``, so a string containing
    ``</script>`` would prematurely close the ``<script>`` element and allow
    injection of arbitrary HTML.  Replacing ``</`` with the equivalent
    escaped sequence ``<\\/`` prevents this while remaining valid JS.

    Args:
        value: Any JSON-serialisable value.

    Returns:
        JSON string with ``</`` replaced by ``<\\/``.
    """
    return json.dumps(value).replace("</", r"<\/")


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
    # Basic CSP — allow scripts/styles only from same origin, no inline eval.
    # 'unsafe-inline' for script-src is required for the window globals injected
    # by _handle_ha_index; without it the browser silently blocks the inline
    # <script> and the pairing UI initialises with wrong base URLs.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
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
        timestamp:   Monotonic timestamp (time.monotonic()) when the activation was recorded.
    """

    gw_id: str
    product_key: str
    local_key: str
    ip_address: str
    sw_ver: str = ""
    timestamp: float = field(default_factory=time.monotonic)
    consumed: bool = False  # R40-F1: set True after first successful read (burn-after-read)


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
        # Provisioning token → flow_id mapping (R28-1: prevents credential fan-out
        # to unrelated concurrent flows).  Populated by _handle_bind_token and
        # _handle_wifi_ap_pair when the caller provides flow_id.
        self._token_to_flow: dict[str, str] = {}
        # Auto-stop task (stops server 60s after last flow unregisters)
        self._auto_stop_task: asyncio.Task[None] | None = None
        # Per-IP rate limit: maps IP → list of request timestamps (SEC-001)
        self._rate_limit: dict[str, list[float]] = {}
        # R50-F6: Timestamp of the last full _rate_limit table sweep. Throttle to
        # once per 5 s to avoid O(n) dict rebuild on every request under IP-flood.
        self._rate_limit_last_cleanup: float = 0.0
        # Guard: only register HA views once per server instance
        self._ha_views_registered: bool = False
        # Strong references to background tasks so GC doesn't cancel them (RUF006)
        self._background_tasks: set[asyncio.Task[None]] = set()
        # APs currently being paired — prevents duplicate concurrent pairing tasks
        self._wifi_ap_pairing_in_progress: set[str] = set()
        # Cached static HTML (file read + path rewrites) for _handle_ha_index.
        # _ha_index_html_mtime tracks the file's mtime at cache build time so
        # the cache is invalidated if index.html is updated while HA is running.
        self._ha_index_html_cache: str | None = None
        self._ha_index_html_mtime: float | None = None
        # Tokenless activations keyed by gw_id — deduplication for devices that
        # retry the activation POST without a provisioning token (e.g. BLE path).
        self._tokenless_results: dict[str, ActivationResult] = {}
        # Cached raw bytes for _handle_index (pairing UI served on port 8099 directly).
        # Invalidated when index.html mtime changes so live-edit during development works.
        self._index_html_cache: bytes | None = None
        self._index_html_mtime: float | None = None

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
        # Cancel any in-progress WiFi AP pairing background tasks so they don't
        # outlive the server (e.g. during HA restart or integration unload).
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._wifi_ap_pairing_in_progress.clear()

        # Signal all SSE subscribers to close.
        # Use put_nowait() instead of await put() — if a queue is full (stalled
        # client), await would block HA shutdown indefinitely.  A suppressed
        # QueueFull is safe here because the runner.cleanup() below will close
        # the TCP connection regardless, causing ConnectionResetError in the
        # _handle_sse loop.
        for q in list(self._sse_queues):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)
        self._sse_queues.clear()

        # Discard pending flow IDs — they belong to the current session and
        # must not carry over if the server is restarted (e.g. new provisioning).
        self._pending_flows.clear()
        self._token_to_flow.clear()
        self._results.clear()

        if self._auto_stop_task is not None:
            self._auto_stop_task.cancel()
            self._auto_stop_task = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
        # R37-2: Clear HTML cache so a subsequent start() (e.g. after HACS update)
        # re-reads index.html from disk rather than serving a potentially stale version.
        self._ha_index_html_cache = None
        self._ha_index_html_mtime = None
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
        # R29-1: Also evict any token→flow bindings for this flow.  If the
        # user cancelled the config flow before the device activated, the
        # bind-token entry would otherwise accumulate until server stop.
        stale = [tok for tok, fid in self._token_to_flow.items() if fid == flow_id]
        for tok in stale:
            del self._token_to_flow[tok]
        if not self._pending_flows and self._auto_stop_task is None:
            self._auto_stop_task = self._hass.async_create_task(
                self._auto_stop_after_idle(), name="tuya-cloudless-auto-stop"
            )
            self._auto_stop_task.add_done_callback(
                lambda t: (
                    t.exception()
                    and _LOGGER.error(
                        "Auto-stop task raised unexpected exception: %s", t.exception()
                    )
                    if not t.cancelled()
                    else None
                )
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
        # R33-1: bind-token is NOT exposed on the unauthenticated port-8099 server.
        # It is available only via the HA HTTPS authenticated endpoint
        # (_PairingBindTokenView, requires_auth=True).  The WiFi-AP path performs
        # inline binding inside _handle_wifi_ap_pair; the BLE path calls the HA
        # HTTPS endpoint when the page is served via HA (the normal case), and
        # gracefully degrades to the unbound fallback when 8099 is used directly.
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
        # R47-F3: Cache the file bytes in memory; invalidate on mtime change to
        # avoid blocking the event loop on disk I/O for every request.
        try:
            current_mtime = index_path.stat().st_mtime
        except OSError:
            current_mtime = None
        if self._index_html_cache is None or current_mtime != self._index_html_mtime:
            # R50-F5: Guard against oversized or unreadable index.html
            # (e.g. a HACS partial-update that swaps in a large file).
            try:
                file_size = index_path.stat().st_size
            except OSError:
                file_size = 0
            if file_size > _MAX_STATIC_FILE_BYTES:
                return web.Response(
                    status=503,
                    text="Pairing UI file too large — this is a bug, please report it.",
                    headers=_SECURITY_HEADERS,
                )
            try:
                self._index_html_cache = index_path.read_bytes()
            except OSError:
                return web.Response(
                    status=503,
                    text="Pairing UI temporarily unavailable — please retry",
                    headers=_SECURITY_HEADERS,
                )
            self._index_html_mtime = current_mtime
        return web.Response(
            body=self._index_html_cache,
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
                "wifi_scan_available": shutil.which("nmcli") is not None,
                "integration_version": _INTEGRATION_VERSION,
            },
            # No ACAO header — the pairing UI is served from the same origin
            # (port 8099).  A wildcard header would let any web page on the LAN
            # read the activator_url (HA's internal IP), leaking network topology.
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
        # R50-F1: Guard against oversized icon file; cap at _MAX_STATIC_FILE_BYTES.
        try:
            icon_size = icon_path.stat().st_size
        except OSError:
            return web.Response(status=404, text="Icon not found")
        if icon_size > _MAX_STATIC_FILE_BYTES:
            _LOGGER.warning(
                "Brand icon exceeds size limit (%d > %d) — denied",
                icon_size,
                _MAX_STATIC_FILE_BYTES,
            )
            return web.Response(status=403, text="Icon file too large")
        try:
            icon_bytes = icon_path.read_bytes()
        except OSError:
            return web.Response(status=503, text="Icon temporarily unavailable")
        return web.Response(
            body=icon_bytes,
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
        client_ip = self._get_client_ip(request)
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
            headers={"Cache-Control": "no-cache"},
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

        client_ip = self._get_client_ip(request)

        # Per-IP rate limiting (SEC-001 / PLAT-824)
        if self._is_rate_limited(client_ip):
            return web.Response(status=429, text="Too Many Requests")

        _LOGGER.debug("Activation request from %s", client_ip)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            body = {}

        # Guard: a valid JSON primitive (null, "string", 123) passes the decoder but
        # is not a dict. Treat such payloads as empty so .get() calls are safe.
        if not isinstance(body, dict):
            body = {}

        # Support both top-level and nested ``data`` field
        raw_data = body.get("data", body)
        data: dict[str, object] = raw_data if isinstance(raw_data, dict) else body

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

        # Validate token format — must be 32 lowercase hex chars (secrets.token_hex(16) = 16 bytes).
        # Tokens not matching this format can never be retrieved via the GET endpoint
        # (which enforces the same regex), creating an unreachable result entry.
        if token and not _TOKEN_RE.match(token):
            _LOGGER.warning("Activation request rejected: invalid token format")
            return web.Response(status=400, text="Invalid token format")

        # Validate gw_id and product_key character set — Tuya IDs are alphanumeric ASCII.
        # Rejects control characters that could interfere with SSE format or HA config flow.
        if not gw_id or not _DEVICE_ID_RE.match(gw_id):
            _LOGGER.warning("Activation request rejected: missing or invalid gw_id format")
            return web.Response(status=400, text="Invalid gw_id format")
        if product_key and not _DEVICE_ID_RE.match(product_key):
            _LOGGER.warning("Activation request rejected: invalid product_key format")
            return web.Response(status=400, text="Invalid product_key format")
        if sw_ver and not _SW_VER_RE.match(sw_ver):
            _LOGGER.warning("Activation request rejected: invalid characters in sw_ver")
            return web.Response(status=400, text="Invalid sw_ver")

        _LOGGER.info(
            "Tuya device activation: gw_id=%s product_key=%s token=%s… sw_ver=%s ip=%s",
            gw_id or "<empty>",
            product_key or "<empty>",
            (token or "<empty>")[:8],  # token is a session credential — log prefix only
            sw_ver or "<empty>",
            client_ip,
        )

        # Idempotency guard: if this token+gw_id was already activated, reuse the
        # existing local_key rather than generating a new one.  Tuya device firmware
        # may retry the activation POST if it doesn't receive a response in time.
        # Generating a different key on a retry would cause a key mismatch when the
        # user configures HA using the key shown on the done screen.
        # R34-4: Mark duplicate activations so we skip flow-resume below.  Re-resuming
        # a flow that already received credentials on the first call could inject old
        # credentials into a concurrently registered, unrelated config flow.
        _is_duplicate_activation = bool(
            token and token in self._results and self._results[token].gw_id == gw_id
        )
        # R46-F5: For tokenless activations (BLE path), deduplicate by gw_id so
        # firmware retries get the same local_key instead of a new random one.
        _is_tokenless_duplicate = bool(not token and gw_id in self._tokenless_results)
        if _is_duplicate_activation:
            result = self._results[token]
            local_key = result.local_key
            _LOGGER.info(
                "Duplicate activation gw_id=%s token=%s… — reusing local_key [REDACTED]",
                gw_id,
                token[:8],
            )
        elif _is_tokenless_duplicate:
            result = self._tokenless_results[gw_id]
            local_key = result.local_key
            _LOGGER.info(
                "Duplicate tokenless activation gw_id=%s — reusing local_key [REDACTED]",
                gw_id,
            )
        else:
            # Generate a random 16-character local_key.
            # secrets.token_hex(8) produces 8 random bytes encoded as 16 lowercase hex
            # chars — the correct length expected by config_flow (_LOCAL_KEY_LENGTH = 16)
            # and by the coordinator (key_bytes = local_key.encode("utf-8") → 16 bytes).
            local_key = secrets.token_hex(_LOCAL_KEY_BYTES // 2)

            result = ActivationResult(
                gw_id=gw_id,
                product_key=product_key,
                local_key=local_key,
                ip_address=client_ip,
                sw_ver=sw_ver,
            )

            if token:
                self._expire_old_results()
                if len(self._results) >= _MAX_STORED_RESULTS:
                    _LOGGER.warning(
                        "Activation result table full (%d entries) — discarding oldest entry",
                        _MAX_STORED_RESULTS,
                    )
                    oldest = min(self._results, key=lambda t: self._results[t].timestamp)
                    del self._results[oldest]
                self._results[token] = result
            else:
                # Tokenless path: store by gw_id for deduplication (R46-F5).
                # R47-F1: apply same TTL-sweep + size cap as the token-keyed table.
                self._expire_old_results()
                if len(self._tokenless_results) >= _MAX_STORED_RESULTS:
                    oldest = min(
                        self._tokenless_results,
                        key=lambda g: self._tokenless_results[g].timestamp,
                    )
                    del self._tokenless_results[oldest]
                self._tokenless_results[gw_id] = result

        # Notify SSE subscribers.  local_key, ip_address, and token are
        # intentionally excluded — all are sensitive and the SSE stream is
        # accessible to any LAN host (R33-3).  The browser already holds the
        # token (returned by wifi-ap-pair or generated in JS for BLE), so it
        # does not need the token from the SSE event.  Only gw_id is included
        # to let the browser confirm which device activated.
        # R40-F5: Fire-and-forget SSE broadcast so we don't delay the HTTP response
        # to the device.  _broadcast_sse can block up to 10 s (10 stalled clients x
        # 1 s timeout each) — longer than the device's own activation-POST timeout.
        event_data = {
            "gw_id": gw_id,
        }
        # R41-F2: Use hass.async_create_task (not deprecated asyncio.get_event_loop().
        # create_task) and hold a strong reference via _background_tasks to prevent
        # silent GC cancellation under memory pressure.
        _sse_task = self._hass.async_create_task(
            self._broadcast_sse("activated", json.dumps(event_data)),
            name="tuya-cloudless-sse-activated",
        )
        self._background_tasks.add(_sse_task)
        _sse_task.add_done_callback(self._background_tasks.discard)

        # Resume any waiting HA config flows.
        # Only resume when token is non-empty (R27-5): a tokenless POST from any
        # LAN host would otherwise inject arbitrary gw_id/local_key into ALL active
        # flows.  BLE and WiFi-AP pairing always generate a 32-hex-char token, so
        # legitimate activations always have one.  Tokenless devices get their result
        # stored but must have a human manually copy the key from the result endpoint.
        # R34-4: Skip flow-resume for duplicate (idempotent) activations — the flow
        # already received its credentials on the first call.  Re-resuming would
        # inject old credentials from a completed session into a concurrently
        # registered, unrelated config flow.
        if token and self._pending_flows and not _is_duplicate_activation:
            flow_data = {
                "gw_id": gw_id,
                "local_key": local_key,
                "ip_address": client_ip,
                "product_key": product_key,
            }

            async def _resume_flow(fid: str) -> None:
                # R41-F3: Re-check membership immediately before async_configure —
                # the flow may have been cancelled between task creation and execution.
                if fid not in self._pending_flows:
                    return
                try:
                    await self._hass.config_entries.flow.async_configure(fid, flow_data)
                except Exception:  # flow may have been cancelled or removed by user
                    _LOGGER.debug("Flow %s no longer active — skipping resume", fid)
                finally:
                    # Always clean up — prevents stale IDs from accumulating and
                    # keeps _pending_flows accurate for auto-stop logic.
                    self._pending_flows.discard(fid)

            # R28-1: If this token was pre-bound to a specific flow via
            # bind-token / wifi-ap-pair, only resume that flow.  This prevents
            # credentials from being fanned out to unrelated concurrent pairing
            # sessions when two users initiate pairing simultaneously.
            # Fall back to resuming all pending flows when no binding exists
            # (e.g. BLE path without bind-token, or older client versions).
            bound_flow = self._token_to_flow.pop(token, None)
            flows_to_resume = (
                [bound_flow]
                if bound_flow and bound_flow in self._pending_flows
                else list(self._pending_flows)
            )
            for flow_id in flows_to_resume:
                # R44-F2: Keep a strong reference so the GC cannot silently
                # cancel the task before the first await (RUF006 pattern).
                _rt = self._hass.async_create_task(
                    _resume_flow(flow_id),
                    name="tuya-cloudless-resume-flow",
                )
                self._background_tasks.add(_rt)
                _rt.add_done_callback(self._background_tasks.discard)

        # Respond in Tuya cloud activation format.
        # R53-F3: For tokenless duplicate activations, omit the localKey from the
        # response body.  The legitimate device already received the key on the
        # first call; this is a firmware retry that only needs an ACK.  Returning
        # the key again makes port 8099 an oracle for any LAN host that knows the
        # gw_id and can craft a tokenless POST (no auth is required on port 8099).
        response_local_key = "" if _is_tokenless_duplicate else local_key
        response_body = {
            "t": int(time.time()),
            "success": True,
            "result": {
                "gwId": gw_id,
                "active": 2,
                "ability": 0,
                "localKey": response_local_key,
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
        # Rate-limit before token lookup to prevent brute-force enumeration
        client_ip = self._get_client_ip(request)
        if self._is_rate_limited(client_ip, max_requests=30, window=60.0):
            return web.Response(
                status=429,
                text="Too Many Requests",
                headers={"Retry-After": "5"},
            )
        token = request.match_info["token"]
        # Validate token format — 32 lowercase hex chars (see _TOKEN_RE).
        # Reject anything else before dict lookup.
        if not _TOKEN_RE.match(token):
            return web.Response(status=404, text="Not Found")
        result = self.get_result(token)

        if result is None:
            return web.json_response(
                {"status": "pending"},
                status=202,
                headers={"Cache-Control": "no-cache"},
            )

        # R40-F1: Burn-after-read — refuse a second fetch of local_key.
        # The browser retrieves the key exactly once after the SSE "activated" event.
        # Any subsequent GET (replay, brute-force enumeration) gets 404.
        if result.consumed:
            return web.Response(status=404, text="Not Found")

        # R48-F1: Mark consumed BEFORE building the response.
        # web.json_response() with plain str/int values cannot raise; there is no
        # practical retry scenario that would require the token to remain valid
        # after this point.  Setting consumed first eliminates any ambiguity about
        # the window between the guard check above and the flag assignment.
        result.consumed = True
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

    async def _handle_bind_token(self, request: web.Request) -> web.Response:
        """Bind a provisioning token to a specific config flow (R28-1).

        Called by the pairing UI before provisioning starts — associates the
        provisioning token the device will use with the flow that initiated the
        pairing session.  When the device later activates with that token,
        only the bound flow receives the credentials instead of ALL pending flows.

        Body JSON:
            ``{"flow_id": "<HA flow UUID>", "token": "<32 hex chars>"}``

        Args:
            request: Incoming HTTP request from the browser.

        Returns:
            204 No Content on success, or 4xx on validation failure.
        """
        # R48-F2: Rate limit to prevent token-binding flood that could evict
        # legitimate bindings via the hard cap (20 binds/min per source IP).
        client_ip = self._get_client_ip(request)
        if self._is_rate_limited(client_ip, max_requests=20, window=60.0):
            return web.Response(
                status=429,
                text="Too Many Requests",
                headers={"Retry-After": "15"},
            )

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return web.Response(status=400, text="Invalid JSON body")

        if not isinstance(body, dict):
            return web.Response(status=400, text="Request body must be a JSON object")

        token = str(body.get("token") or "")
        flow_id = str(body.get("flow_id") or "")

        if not _TOKEN_RE.match(token):
            return web.Response(status=400, text="Invalid token format")

        # Only allow binding to flows that are actually registered — prevents an
        # attacker from poisoning future flows before they register.
        if not flow_id or flow_id not in self._pending_flows:
            return web.Response(status=400, text="flow_id not found in pending flows")

        # Reject rebinding — first caller wins; prevents a race where a second
        # concurrent session claims the same token.
        if token in self._token_to_flow:
            return web.Response(status=409, text="Token already bound")

        # R41-F8: Hard cap prevents unbounded growth from a browser repeatedly
        # refreshing the pairing page and generating new tokens without activating.
        # Evict the oldest binding when the cap is reached.
        _MAX_TOKEN_BINDINGS = 256
        if len(self._token_to_flow) >= _MAX_TOKEN_BINDINGS:
            oldest = next(iter(self._token_to_flow))
            del self._token_to_flow[oldest]
            _LOGGER.debug("token_to_flow cap reached — evicted oldest binding")
        self._token_to_flow[token] = flow_id
        _LOGGER.debug("Bound token %s… to flow %s", token[:8], flow_id)
        return web.Response(status=204)

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        """Server-Sent Events stream for real-time activation notifications.

        The browser connects here and receives an ``activated`` event when the
        device calls the fake-cloud endpoint.

        Args:
            request: Incoming HTTP request from the browser.

        Returns:
            SSE stream response (kept open until client disconnects).
        """
        # Per-IP rate limit — prevents rapid open/close cycling that could exhaust
        # the global SSE connection slot table for legitimate browsers.
        client_ip = self._get_client_ip(request)
        if self._is_rate_limited(client_ip, max_requests=10, window=60.0):
            return web.Response(
                status=429,
                text="Too Many Requests",
                headers={"Retry-After": "10"},
            )

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
                **_SECURITY_HEADERS,
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )

        # Include response.prepare() inside try/finally so the queue is removed
        # even if the client disconnects before the headers are sent (race condition
        # that would otherwise leave an orphaned entry in _sse_queues forever).
        try:
            await response.prepare(request)

            # Send initial keep-alive comment
            await response.write(b": connected\n\n")

            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    # Send SSE keep-alive comment (prevents proxy timeouts).
                    # If the write fails the client has disconnected; exit cleanly.
                    try:
                        await response.write(b": keepalive\n\n")
                    except OSError:
                        break
                    continue

                if message is None:
                    break

                await response.write(message.encode())
        except asyncio.CancelledError:
            raise  # Re-raise to properly signal task cancellation to asyncio
        except (ConnectionResetError, OSError):
            # R32-3: BrokenPipeError (EPIPE) is an OSError subclass raised when the
            # browser closes the tab mid-stream. Catch it alongside ConnectionResetError
            # to avoid spurious ERROR-level log entries on normal client disconnects.
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
        client_ip = self._get_client_ip(request)
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
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            except TimeoutError:
                # Kill the subprocess so it doesn't linger as a zombie; then re-raise
                # so the outer except can log and return an empty list.
                # R48-F3: reap the child process after kill() to prevent zombies.
                # Wrap with wait_for so tests can patch asyncio.wait_for for fast teardown.
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=2.0)
                raise
            result: list[str] = []
            for line in stdout.decode(errors="replace").splitlines():
                ssid = line.strip()
                # R40-F11: Match _get_default_ssid — cap at 32 bytes (WiFi 802.11 spec limit)
                if (
                    ssid
                    and ssid != "--"
                    and not _CTRL_CHAR_RE.search(ssid)
                    and len(ssid.encode()) <= 32
                ):
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

        # R46-F7: Cap total SSID list to limit data exposure; current SSID (already
        # at index 0) is always included.  Rate-limiting (5/min) is the primary
        # defence; this bound also caps response payload size.
        # R51-F4: Build tuya_aps AFTER the cap so it is also bounded by
        # _MAX_SSID_SCAN_RESULTS (previously built before the slice, doubling payload).
        ssids = ssids[:_MAX_SSID_SCAN_RESULTS]
        tuya_aps = [{"ssid": s} for s in ssids if _is_tuya_ap(s)]

        return web.json_response(
            {"ssids": ssids, "current_ssid": current_ssid, "tuya_aps": tuya_aps},
            # No ACAO — same-origin endpoint (port 8099).  Wildcard would let
            # cross-origin pages read nearby WiFi SSIDs, leaking location data.
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
        client_ip = self._get_client_ip(request)
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
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            except TimeoutError:
                # R49-F2: reap zombie after kill (same pattern as R48-F3 in _run/_run_nmcli).
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=2.0)
                raise
            seen: set[str] = set()
            for line in stdout.decode(errors="replace").splitlines():
                ssid = line.strip()
                if (
                    ssid
                    and ssid != "--"
                    and not _CTRL_CHAR_RE.search(ssid)
                    # R52-F1: Apply the same 32-byte SSID limit as _run_nmcli
                    # (802.11 spec max) for consistent validation across all paths.
                    and len(ssid.encode()) <= 32
                    and ssid not in seen
                    and _is_tuya_ap(ssid)
                ):
                    seen.add(ssid)
                    tuya_aps.append({"ssid": ssid})
        except (FileNotFoundError, TimeoutError, OSError) as exc:
            _LOGGER.debug("Quick WiFi scan unavailable: %s", exc)

        # R52-F2: Cap response list to prevent oversized JSON body when nmcli
        # cache contains many Tuya-prefixed SSIDs (e.g. large corporate network).
        tuya_aps = tuya_aps[:_MAX_SSID_SCAN_RESULTS]
        return web.json_response(
            {"tuya_aps": tuya_aps},
            # No ACAO — same-origin endpoint (port 8099).
        )

    async def _handle_wifi_ap_pair(self, request: web.Request) -> web.Response:
        """Initiate WiFi-AP provisioning for a Tuya device in AP mode.

        Connects the HA host to the Tuya device's AP, sends credentials, then
        reconnects to the home WiFi.  The device joins home WiFi and POSTs to
        the fake-cloud endpoint, triggering the standard SSE activation flow.

        Body JSON:
            ``{"ap_ssid": "SmartLife-AB12", "home_ssid": "HomeNet", "home_password": "…"}``

        Args:
            request: Incoming HTTP request from the browser.

        Returns:
            JSON: ``{"token": "...", "events_url": "/api/provision/events"}``
            Returns immediately; pairing happens in the background.
        """
        content_type = request.content_type or ""
        if not content_type.startswith("application/json"):
            return web.Response(status=415, text="Content-Type must be application/json")

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return web.Response(status=400, text="Invalid JSON body")

        if not isinstance(body, dict):
            return web.Response(status=400, text="Request body must be a JSON object")

        ap_ssid = str(body.get("ap_ssid") or "").strip()
        home_ssid = str(body.get("home_ssid") or "").strip()
        home_password = str(body.get("home_password") or "")
        # Optional: caller's config flow ID — used to bind the generated token to this
        # specific flow so only it receives the credentials on activation (R28-1).
        # R40-F7: Validate format before membership test to prevent hash-DoS from
        # an attacker submitting a very long string as flow_id.
        _raw_hint = str(body.get("flow_id") or "")
        flow_id_hint = _raw_hint if _FLOW_ID_RE.match(_raw_hint) else ""

        if not ap_ssid or not home_ssid:
            return web.Response(status=400, text="ap_ssid and home_ssid are required")

        # Reject SSIDs that are not recognised Tuya device APs — prevents a malicious
        # client from tricking the server into connecting HA's WiFi to an arbitrary network.
        if not _is_tuya_ap(ap_ssid):
            return web.Response(
                status=400, text="ap_ssid must be a known Tuya device AP (unrecognised prefix)"
            )

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

        # Per-IP rate limit — spawns nmcli + background task: 5/min per IP.
        # Applied after input validation so 400 errors are not penalised.
        client_ip = self._get_client_ip(request)
        if self._is_rate_limited(client_ip, max_requests=5, window=60.0):
            return web.Response(
                status=429,
                text="Too Many Requests",
                headers={"Retry-After": "12"},
            )

        # Prevent duplicate concurrent pairing tasks for the same AP
        if ap_ssid in self._wifi_ap_pairing_in_progress:
            return web.Response(
                status=409, text="WiFi AP pairing already in progress for this device"
            )

        # R43-F1: Cap concurrent WiFi-AP tasks using the dedicated set, not the
        # shared _background_tasks (which also holds SSE/flow-resume tasks and
        # would give a false count, blocking legitimate pairing requests).
        if len(self._wifi_ap_pairing_in_progress) >= _MAX_WIFI_AP_TASKS:
            return web.Response(
                status=429, text="Too many pairing operations in progress — please wait and retry"
            )

        # Fail fast if nmcli is unavailable — avoids silent 120s timeout for user
        if shutil.which("nmcli") is None:
            return web.Response(
                status=503,
                text="nmcli not available; WiFi AP pairing requires NetworkManager",
            )

        token = secrets.token_hex(_PROVISION_TOKEN_BYTES)
        activator_url = self.ha_local_url()

        # Bind token to the requesting flow (R28-1): if the caller provided a valid
        # registered flow_id, only that flow receives credentials on activation.
        if flow_id_hint and flow_id_hint in self._pending_flows:
            self._token_to_flow[token] = flow_id_hint

        self._wifi_ap_pairing_in_progress.add(ap_ssid)
        task = self._hass.async_create_task(
            self._wifi_ap_pair_task(ap_ssid, home_ssid, home_password, token, activator_url),
            name="tuya-cloudless-wifi-ap-pair",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(lambda _: self._wifi_ap_pairing_in_progress.discard(ap_ssid))

        return web.json_response(
            {"token": token, "events_url": "/api/provision/events"},
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
        # R36-2: Track whether step 2 (Tuya AP connect) succeeded.  The finally
        # block must only attempt home-WiFi reconnect when the host's WiFi was
        # actually changed; otherwise an unauthenticated caller can force the
        # host to join an arbitrary network via a crafted home_ssid.
        _connected_to_tuya_ap = False

        async def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
            """Run a shell command and return (returncode, stdout)."""
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except (TimeoutError, asyncio.CancelledError):
                # Kill the subprocess on both timeout AND task cancellation.
                # R48-F3: await proc.communicate() after kill() to reap the child
                # process and prevent it from lingering as a zombie in the OS
                # process table (zombies accumulate until the parent—HA—exits).
                # Wrap with wait_for so tests can patch asyncio.wait_for for fast teardown.
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=2.0)
                raise
            return proc.returncode or 0, stdout.decode(errors="replace").strip()

        try:
            # Step 1: find current active connection name (for restore later)
            rc, out = await _run(
                ["nmcli", "--terse", "--fields", "ACTIVE,NAME", "connection", "show"]
            )
            if rc == 0:
                for line in out.splitlines():
                    if line.startswith("yes:"):
                        candidate = line[4:].strip() or None
                        if candidate and (_CTRL_CHAR_RE.search(candidate) or len(candidate) > 255):
                            _LOGGER.warning("WiFi AP pair: ignoring suspicious connection name")
                            candidate = None
                        prev_connection = candidate
                        break

            # Step 2: connect to the Tuya AP (open network)
            # R33-4: "--" end-of-options separator prevents nmcli from
            # interpreting an SSID starting with "--" as a flag.
            _LOGGER.info("WiFi AP pair: connecting to %s", ap_ssid)
            rc, _ = await _run(
                ["nmcli", "device", "wifi", "connect", "--", ap_ssid],
                timeout=15.0,  # Tuya APs are open; 15s is generous
            )
            if rc != 0:
                raise OSError(f"nmcli connect to {ap_ssid!r} failed (rc={rc})")
            _connected_to_tuya_ap = True  # R36-2: host WiFi has been changed

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
                        timeout=_aiohttp.ClientTimeout(
                            total=_TUYA_AP_GW_TIMEOUT,
                            connect=3.0,  # Limit TCP handshake phase; device switches quickly
                        ),
                        allow_redirects=False,  # Prevent SSRF via device firmware redirect
                    ) as resp,
                ):
                    _LOGGER.debug("gw.json response: %s", resp.status)
            except Exception as exc:
                # Expected — device switches WiFi mid-request; log and continue
                _LOGGER.debug("gw.json POST ended early (device switching WiFi): %s", exc)

        except (FileNotFoundError, OSError) as exc:
            # R51-F3: Zero password immediately — exc.__traceback__ exposes frame locals
            # to crash reporters; clearing it here prevents leakage before any logging.
            home_pwd = ""
            # R37-5: Log only the exception type, not its str(). aiohttp OSError
            # subclasses may embed the request body (which contains WiFi password)
            # in their string representation.
            _LOGGER.warning("WiFi AP pair task failed: %s", type(exc).__name__)
            await self._broadcast_sse(
                "wifi_ap_error",
                # R35-9: token excluded from SSE broadcast
                json.dumps({"error": "WiFi control unavailable or connect failed"}),
            )
        except Exception as exc:
            # R51-F3: Same zero-out for the catch-all handler.
            home_pwd = ""
            # Use warning (not exception) to avoid printing a traceback that
            # could contain the WiFi password from the call-stack locals.
            _LOGGER.warning("Unexpected error in WiFi AP pair task: %s", type(exc).__name__)
            await self._broadcast_sse(
                "wifi_ap_error",
                json.dumps({"error": "Unexpected pairing error"}),  # R35-9: token excluded
            )
        finally:
            # Step 4: always try to restore connectivity.
            # R31-1: Shield each _run call so HA shutdown (task.cancel()) cannot abort
            # the cleanup mid-execution, which would leave the host's WiFi connected to
            # the Tuya device's provisioning AP instead of the home network.
            # asyncio.shield() protects the inner coroutine from cancellation; the
            # CancelledError is caught here so cleanup runs to completion.
            if prev_connection:
                _LOGGER.info("WiFi AP pair: reconnecting to %s", prev_connection)
                # R50-F7: Password not needed in this branch — zero it out before any
                # potential exception so exc.__traceback__ cannot expose it.
                home_pwd = ""
                try:
                    await asyncio.shield(
                        _run(
                            # R33-4: "--" prevents profile names starting with
                            # "--" from being misinterpreted as nmcli flags.
                            ["nmcli", "connection", "up", "--", prev_connection],
                            timeout=_TUYA_AP_RECONNECT_TIMEOUT,
                        )
                    )
                except (Exception, asyncio.CancelledError) as exc:
                    _LOGGER.warning("WiFi AP pair: reconnect failed: %s", exc)
            elif _connected_to_tuya_ap:
                # No saved connection — reconnect to home WiFi directly so we
                # don't remain stuck on the Tuya AP after provisioning.
                # R36-2: Only run this when we actually connected to the Tuya AP
                # (i.e., the host's WiFi was changed).  Skipping when step 2 never
                # succeeded prevents an unauthenticated caller from forcing the host
                # to join an arbitrary network by supplying a crafted home_ssid.
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
                        "--",  # R33-4: end-of-options before user-supplied SSID
                        home_ssid,
                    ]
                    if home_pwd:
                        cmd += ["password", home_pwd]
                    # R50-F7: Erase password from frame locals immediately after last
                    # use so it is not accessible via exc.__traceback__ in any subsequent
                    # exception (crash reporters, HA diagnostic collectors).
                    home_pwd = ""
                    await asyncio.shield(_run(cmd, timeout=_TUYA_AP_RECONNECT_TIMEOUT))
                except (Exception, asyncio.CancelledError) as exc:
                    _LOGGER.warning("WiFi AP pair: home WiFi reconnect failed: %s", exc)
            else:
                # Neither reconnect branch will run — zero password defensively.
                home_pwd = ""

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
                headers=_SECURITY_HEADERS,
            )

        # R43-F6: Invalidate cache if index.html was modified on disk (e.g. HACS
        # update while HA is running).  The mtime check is cheap (single stat(2)).
        # R49-F3: Guard stat() — NFS/HACS mid-write can raise OSError; fall back to
        # unconditional re-read rather than returning a 500 traceback.
        try:
            current_mtime: float | None = index_path.stat().st_mtime
        except OSError:
            current_mtime = None
        if self._ha_index_html_cache is None or self._ha_index_html_mtime != current_mtime:
            # R51-F1: Apply the same size guard as _handle_index (R50-F5) so a large
            # index.html placed by HACS does not exhaust memory on every HTTPS request.
            try:
                file_size = index_path.stat().st_size
            except OSError:
                file_size = 0
            if file_size > _MAX_STATIC_FILE_BYTES:
                return web.Response(
                    status=503,
                    text="Pairing UI file too large — this is a bug, please report it.",
                    headers=_SECURITY_HEADERS,
                )
            try:
                html_raw = index_path.read_text(encoding="utf-8")
            except OSError:
                return web.Response(
                    status=503,
                    text="Pairing UI temporarily unavailable — please retry",
                    headers=_SECURITY_HEADERS,
                )
            static_base = _HA_PAIRING_PREFIX + "/static"
            provision_base = _HA_PAIRING_PREFIX + "/provision"
            # Rewrite /static/ asset references to the HA-relative path
            html_raw = html_raw.replace('="/static/', f'="{static_base}/')
            # Rewrite QR image src to HA path (404 → JS error handler shows "unavailable")
            html_raw = html_raw.replace(
                'src="/api/provision/qr.svg"',
                f'src="{provision_base}/qr.svg"',
            )
            self._ha_index_html_cache = html_raw
            self._ha_index_html_mtime = current_mtime

        html = self._ha_index_html_cache
        static_base = _HA_PAIRING_PREFIX + "/static"
        provision_base = _HA_PAIRING_PREFIX + "/provision"
        activator_url = self.ha_local_url()  # Always HTTP — Tuya device cannot do TLS

        # Inject window globals before </head> so app.js reads them on load.
        # Use _json_in_script() rather than json.dumps() directly: if any value
        # contains "</script>" the raw sequence would close the tag and allow
        # arbitrary HTML injection.
        globals_script = (
            "<script>\n"
            f"window._TUYA_ACTIVATOR_BASE={_json_in_script(activator_url)};\n"
            f"window._TUYA_PROVISION_BASE={_json_in_script(provision_base)};\n"
            f"window._TUYA_STATIC_BASE={_json_in_script(static_base)};\n"
            "</script>\n"
        )
        html = html.replace("</head>", globals_script + "</head>", 1)

        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers=_SECURITY_HEADERS,
        )

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
                # R37-1: Keep parity with the port-8099 config endpoint so the
                # pairing UI behaves consistently regardless of which path serves it.
                "wifi_scan_available": shutil.which("nmcli") is not None,
                "integration_version": _INTEGRATION_VERSION,
            },
            # No ACAO — the pairing UI is registered on HA's own server (same origin).
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
            # R53-F6: Require auth — response includes default_ssid (home WiFi SSID)
            # and integration_version.  The pairing UI is always opened from within
            # the authenticated HA frontend, so requiring auth is safe here.
            # (Compare _PairingEventsView which must remain unauthenticated so the
            #  SSE stream can open before the HA auth token exchange completes.)
            requires_auth = True
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
            # requires_auth = True (default) — returns local_key in plaintext;
            # the pairing UI browser has HA auth during the config flow session
            url = _HA_PAIRING_PREFIX + "/provision/result/{token}"
            name = "api:tuya_cloudless:pairing:result"

            async def get(  # type: ignore[override]
                self, request: web.Request, token: str
            ) -> web.Response:
                # HA injects token as a keyword arg from its URL routing.
                # Inject it into match_info so _handle_get_result can read it.
                request.match_info["token"] = token
                return await server._handle_get_result(request)

        class _PairingWifiScanView(HomeAssistantView):
            # requires_auth = True (default) — scans reveal nearby SSIDs/home network
            url = _HA_PAIRING_PREFIX + "/provision/wifi-scan"
            name = "api:tuya_cloudless:pairing:wifi_scan"

            async def get(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_wifi_scan(request)

        class _PairingQuickScanView(HomeAssistantView):
            # requires_auth = True (default) — reveals Tuya AP SSIDs on the LAN
            url = _HA_PAIRING_PREFIX + "/provision/quick-scan"
            name = "api:tuya_cloudless:pairing:quick_scan"

            async def get(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_quick_scan(request)

        class _PairingWifiApPairView(HomeAssistantView):
            # requires_auth = True (default) — executes nmcli; must not be unauthenticated
            url = _HA_PAIRING_PREFIX + "/provision/wifi-ap-pair"
            name = "api:tuya_cloudless:pairing:wifi_ap_pair"

            async def post(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_wifi_ap_pair(request)

        class _PairingBindTokenView(HomeAssistantView):
            # requires_auth = True (default) — R31-4: bind-token on the HA HTTPS path
            # requires authentication so only the legitimate browser session can bind
            # a provisioning token to a config flow.  The unauthenticated port-8099
            # variant remains for HTTP-fallback compatibility, but the BLE/WiFi-AP
            # browser (which is loaded from the HA HTTPS path and has auth) should
            # prefer this endpoint.
            url = _HA_PAIRING_PREFIX + "/provision/bind-token"
            name = "api:tuya_cloudless:pairing:bind_token"

            async def post(  # type: ignore[override]
                self, request: web.Request
            ) -> web.Response:
                return await server._handle_bind_token(request)

        class _PairingStaticView(HomeAssistantView):
            requires_auth = False
            url = _HA_PAIRING_PREFIX + "/static/{path:.+}"
            name = "api:tuya_cloudless:pairing:static"

            async def get(  # type: ignore[override]
                self, request: web.Request, path: str
            ) -> web.Response:
                file_path = (ui_dir / path).resolve()
                # Security: prevent path traversal outside the UI directory.
                # Use Path.is_relative_to() — avoids the prefix-collision bug
                # where "/ui-evil" would incorrectly match a startswith("/ui") check.
                try:
                    ui_resolved = ui_dir.resolve()
                    brand_resolved = _BRAND_DIR.resolve()
                    if not file_path.is_relative_to(ui_resolved):
                        return web.Response(status=403, text="Forbidden")
                except (ValueError, OSError):
                    return web.Response(status=400, text="Bad request")
                if not file_path.is_file():
                    # Fallback: serve brand assets (icon.png, logo.png, etc.)
                    # R51-F2: Guard brand_path.resolve() with OSError to handle
                    # symlink loops or deeply-nested paths that raise on resolution.
                    try:
                        brand_path = (_BRAND_DIR / path).resolve()
                    except OSError:
                        return web.Response(status=400, text="Bad request")
                    if brand_path.is_relative_to(brand_resolved) and brand_path.is_file():
                        file_path = brand_path
                    else:
                        return web.Response(status=404, text="Not found")
                content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                # R49-F4: Guard against oversized files in the UI directory.
                # Path-traversal checks prevent reaching arbitrary FS paths, but a
                # large file accidentally or intentionally placed in pairing_ui/ would
                # be read entirely into memory per request — cap at _MAX_STATIC_FILE_BYTES.
                try:
                    file_size = file_path.stat().st_size
                except OSError:
                    return web.Response(status=404, text="Not found")
                if file_size > _MAX_STATIC_FILE_BYTES:
                    _LOGGER.warning(
                        "Static file %s exceeds size limit (%d > %d) — denied",
                        file_path.name,
                        file_size,
                        _MAX_STATIC_FILE_BYTES,
                    )
                    return web.Response(status=403, text="File too large")
                # R51-F5: Validate actual bytes after read to close the TOCTOU window
                # between stat() and read_bytes() (file can grow between the two calls).
                try:
                    data = file_path.read_bytes()
                except OSError:
                    return web.Response(status=503, text="File temporarily unavailable")
                if len(data) > _MAX_STATIC_FILE_BYTES:
                    _LOGGER.warning(
                        "Static file %s grew past size limit after stat — denied",
                        file_path.name,
                    )
                    return web.Response(status=403, text="File too large")
                return web.Response(
                    body=data,
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
        self._hass.http.register_view(_PairingBindTokenView())
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
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            except TimeoutError:
                # R49-F1: reap zombie after kill to prevent OS process-table accumulation.
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=2.0)
                raise
            for line in stdout.decode(errors="replace").splitlines():
                if line.startswith("yes:"):
                    ssid = line[4:].strip()
                    if (
                        ssid
                        and ssid != "--"
                        and not _CTRL_CHAR_RE.search(ssid)
                        and len(ssid.encode()) <= 32
                    ):
                        return ssid
        except (FileNotFoundError, TimeoutError, OSError) as exc:
            _LOGGER.debug("Default SSID lookup unavailable: %s", exc)
        # fallback: try iwgetid (lightweight, often available without NetworkManager)
        if shutil.which("iwgetid"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "iwgetid",
                    "-r",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
                except TimeoutError:
                    # R49-F1: reap iwgetid zombie on timeout.
                    proc.kill()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(proc.communicate(), timeout=2.0)
                    raise
                ssid = stdout.decode(errors="replace").strip()
                if (
                    ssid
                    and ssid != "--"
                    and not _CTRL_CHAR_RE.search(ssid)
                    and len(ssid.encode()) <= 32
                ):
                    return ssid
            except (FileNotFoundError, TimeoutError, OSError) as exc:
                _LOGGER.debug("iwgetid fallback unavailable: %s", exc)
        return None

    def _get_client_ip(self, request: web.Request) -> str:
        """Return the real client IP, honouring X-Forwarded-For from loopback.

        When HA runs behind a reverse proxy (Nginx, Caddy, Nabu Casa), all
        TCP connections arrive from 127.0.0.1.  In that case we trust the
        ``X-Forwarded-For`` header, which the proxy sets to the browser's IP.
        We only trust this header from the loopback address to prevent spoofing
        from untrusted peers.

        Args:
            request: Incoming aiohttp request.

        Returns:
            Client IP string, never empty ("unknown" as last resort).
        """
        peer = request.remote or ""
        # Trust X-Forwarded-For only when the TCP connection comes from a loopback
        # address, indicating a trusted reverse proxy.  Use ipaddress.is_loopback()
        # rather than an exact-string check so that ::1 variants and 127.x.y.z are
        # all recognised (R26-5).
        try:
            if peer and ipaddress.ip_address(peer).is_loopback:
                forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                if forwarded:
                    try:
                        ipaddress.ip_address(forwarded)
                        return forwarded
                    except ValueError:
                        _LOGGER.debug("Invalid X-Forwarded-For value ignored: %r", forwarded)
        except ValueError:
            pass  # non-IP peer (e.g. unix socket path); fall through to peer/unknown
        return peer or "unknown"

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
        # Requests with no identifiable client IP (unix socket / abstract peer) use the
        # "unknown" sentinel.  Previously these skipped rate-limiting entirely, creating
        # a bypass.  Now they share a single "unknown" bucket — still rate-limited but
        # all anonymous callers share the quota (R26-5).
        # The bucket key is kept as-is; callers are limited collectively.

        now = time.monotonic()
        timestamps = [ts for ts in self._rate_limit.get(client_ip, []) if now - ts < window]
        rate_limited = len(timestamps) >= max_requests
        # Append BEFORE eviction so the current IP is never removed as "empty"
        # even when not rate-limited (window could be empty after filtering).
        if not rate_limited:
            timestamps.append(now)
        self._rate_limit[client_ip] = timestamps
        # R31-2: Always evict stale buckets (empty lists) from ALL IPs — was previously
        # skipped for rate-limited callers, allowing stale entries to accumulate until
        # the hard-cap eviction fired (which could then evict a legitimate user's bucket
        # rather than the oldest attacker IP).
        # R50-F6: Throttle the full dict-rebuild to at most once per 5 s.
        # Under IP-flood, every request was rebuilding the full 1024-entry table (O(n))
        # on the event loop.  The hard-cap eviction path below remains the primary bound.
        if now - self._rate_limit_last_cleanup >= 5.0:
            self._rate_limit = {ip: ts_list for ip, ts_list in self._rate_limit.items() if ts_list}
            self._rate_limit_last_cleanup = now
        if rate_limited:
            _LOGGER.warning(
                "Rate limit exceeded: ip=%s requests=%d",
                client_ip,
                len(timestamps),
            )
            return True
        # Hard cap on number of distinct tracked IPs — prevents memory exhaustion
        # from an IP-rotating attacker filling the dict with many source addresses.
        if len(self._rate_limit) >= _MAX_RATE_LIMIT_IPS:
            oldest_ip = min(
                self._rate_limit,
                key=lambda ip: self._rate_limit[ip][0] if self._rate_limit[ip] else 0,
            )
            del self._rate_limit[oldest_ip]
        return False

    async def _broadcast_sse(self, event: str, data: str) -> None:
        """Push a Server-Sent Event to all connected browsers.

        Uses a 1-second timeout per queue so a stalled client cannot block the
        entire pairing flow.  Queues that time out are removed as stale.

        Args:
            event: SSE event name (e.g. ``"activated"``).
            data:  JSON string payload.
        """
        # R30-2: Log and return instead of raising — a ValueError here would propagate
        # to the aiohttp handler (500 error) or silently kill a background asyncio Task.
        # The guard is defensive; current callers cannot trigger it, but future callers
        # might pass non-JSON strings.  Drop the event rather than crash the pairing flow.
        # R49-F8: Bound event name and data lengths so future callers (or device-
        # controlled gw_id values that reach _broadcast_sse) cannot enqueue large
        # strings into every connected browser queue (_MAX_SSE_CONNECTIONS x maxsize).
        if len(event) > _MAX_SSE_EVENT_LEN:
            _LOGGER.error(
                "SSE event name too long (%d > %d) — dropped",
                len(event),
                _MAX_SSE_EVENT_LEN,
            )
            return
        if len(data) > _MAX_SSE_DATA_LEN:
            _LOGGER.error(
                "SSE data too large (%d > %d bytes) — dropped",
                len(data),
                _MAX_SSE_DATA_LEN,
            )
            return
        # R52-F6: Use regex that also catches Unicode line terminators
        # (U+0085, U+2028, U+2029) to prevent SSE header injection.
        if _SSE_NEWLINE_RE.search(event):
            _LOGGER.error("SSE event name contains illegal newline — event dropped: %r", event[:50])
            return
        if _SSE_NEWLINE_RE.search(data):
            _LOGGER.error("SSE data contains raw newline — event dropped: %r", data[:50])
            return
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
            # Drain the backlog and inject the close sentinel so _handle_sse exits
            # cleanly.  Without this the handler would loop forever sending keepalive
            # comments because no more events will ever be put into the detached queue.
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    break
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)  # best-effort; handler exits on TCP drop if queue stays full

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

        # R47-F1: Also expire tokenless results (no TTL check existed before this fix)
        expired_tokenless = [
            gw_id
            for gw_id, result in self._tokenless_results.items()
            if now - result.timestamp > _RESULT_TTL_SECS
        ]
        for gw_id in expired_tokenless:
            del self._tokenless_results[gw_id]

        # R36-4: Purge _token_to_flow entries whose flow is no longer active.
        # Without this, abandoned pairing flows (no browser cancel) accumulate
        # indefinitely because the activation event (which pops the token) never
        # fires.  Unlike _results, _token_to_flow has no size cap or TTL.
        stale_tokens = [
            tok for tok, fid in self._token_to_flow.items() if fid not in self._pending_flows
        ]
        for tok in stale_tokens:
            del self._token_to_flow[tok]

    async def _auto_stop_after_idle(self) -> None:
        """Stop the server 60 s after the last flow unregisters, if still idle."""
        await asyncio.sleep(60)
        # R49-F5: Drive TTL sweep for tokenless results even when no HTTP polls
        # arrive.  get_result() triggers _expire_old_results() only for the
        # token-based table (poll clients); tokenless results can accumulate until
        # the next activation unless we sweep here too.  The size cap (R47-F1)
        # is the primary bound; this sweep ensures the TTL also fires.
        self._expire_old_results()
        if self._pending_flows:
            # A new flow registered while we were sleeping — do nothing.
            self._auto_stop_task = None
            return

        # Verify we're still the active server before stopping.
        from .const import DOMAIN

        current = self._hass.data.get(DOMAIN, {}).get(_KEY_PAIRING_SERVER)
        if current is self:
            _LOGGER.info("Tuya Cloudless pairing server idle for 60 s — stopping automatically")
            # R34-2: Re-check _pending_flows immediately before stop_pairing_server.
            # A new config flow can register between the check above and the awaited
            # stop call; that flow's activation would be silently discarded by stop().
            if not self._pending_flows:
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

    # Guard against concurrent startup (e.g. two config entries set up at the same
    # time) which would cause both coroutines to call server.start() on the same
    # port, triggering OSError: [Errno 98] Address already in use on the second.
    _LOCK_KEY = "_pairing_server_start_lock"
    if _LOCK_KEY not in domain_data:
        domain_data[_LOCK_KEY] = asyncio.Lock()
    async with domain_data[_LOCK_KEY]:  # type: ignore[union-attr]
        server = domain_data.get(_KEY_PAIRING_SERVER)
        if not isinstance(server, PairingServer):
            server = PairingServer(hass)
            try:
                await server.start()
            except (OSError, asyncio.CancelledError):
                # R33-5: If start() fails or is cancelled mid-way, clean up the
                # partially started server so it does not hold a port or runner
                # reference.  Without this, a cancelled start leaves an orphaned
                # server that stop_pairing_server() can never reach.
                with contextlib.suppress(Exception):
                    await server.stop()
                raise
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
        # Validate flow_id before embedding it in a Location header.
        # HA flow IDs are UUIDs; we accept alphanumeric + hyphens + underscores
        # (max 128 chars). This prevents injecting arbitrary data into the redirect
        # URL and protects against any future header-injection edge cases.
        if not re.match(r"^[a-zA-Z0-9_-]{1,128}$", flow_id):
            return web.Response(status=400, text="Invalid flow_id")
        # Only trust X-Forwarded-Proto from the reverse proxy (loopback).
        # A LAN device spoofing this header must not be able to hijack the redirect scheme.
        raw_peer = request.remote or ""
        proto_header = request.headers.get("X-Forwarded-Proto", "")
        proto = (
            "https"
            if (raw_peer in ("127.0.0.1", "::1") and proto_header == "https")
            else request.url.scheme
        )
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
