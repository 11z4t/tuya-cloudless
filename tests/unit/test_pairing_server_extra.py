"""Extra unit tests for custom_components/tuya_cloudless/pairing_server.

Targets previously uncovered paths:
  - PairingServer.start / stop lifecycle (lines 165-183)
  - ha_local_url() network-helper and internal_url fallback paths (226-231)
  - register_flow() cancel-auto-stop path (247-250)
  - unregister_flow() schedule-auto-stop path (260-262)
  - _handle_index 404 path (294)
  - _handle_config response (325-326)
  - _handle_qr (349-371)
  - SSE handler _handle_sse (525-560)
  - _broadcast_sse helper (609)
  - _auto_stop_after_idle (624-638)
  - Module helpers: get_pairing_server, ensure_pairing_server (656-682)
  - PairingRedirectView (701, 705-707)
  - register_redirect_view (719-724)
  - stop_pairing_server (735-743)
  - /api.json alias endpoint (same activate handler)
  - Malformed JSON body handling in activate
  - sw_ver / camelCase fields in activate body
  - WiFi scan with nmcli returning results
  - WiFi scan timeout handling
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

_REPO = Path(__file__).parent.parent.parent
for _p in [str(_REPO / "lib"), str(_REPO)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from custom_components.tuya_cloudless.pairing_server import (  # noqa: E402
    ActivationResult,
    PairingRedirectView,
    PairingServer,
    _KEY_PAIRING_SERVER,
    ensure_pairing_server,
    get_pairing_server,
    stop_pairing_server,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_hass(*, internal_url: str | None = "http://homeassistant.local:8123") -> MagicMock:
    """Return a minimal HomeAssistant mock."""
    hass = MagicMock()
    hass.config.internal_url = internal_url
    hass.http = MagicMock()
    hass.http.register_view = MagicMock()
    hass.async_create_task = MagicMock(
        side_effect=lambda coro, **kw: asyncio.ensure_future(coro)
    )
    hass.data = {}
    return hass


@pytest.fixture
async def server() -> PairingServer:
    """Return a PairingServer (not started)."""
    return PairingServer(_make_hass(), port=0)


@pytest.fixture
async def client(server: PairingServer) -> TestClient:
    """Start server and return an aiohttp TestClient."""
    test_server = TestServer(server._app)
    cli = TestClient(test_server)
    await cli.start_server()
    yield cli
    await cli.close()


# ── PairingServer.start / stop ────────────────────────────────────────────────


class TestStartStop:
    async def test_start_creates_runner_and_site(self) -> None:
        """start() sets up runner and site, registers redirect view."""
        hass = _make_hass()
        server = PairingServer(hass, port=0)

        with (
            patch("custom_components.tuya_cloudless.pairing_server.web.AppRunner") as mock_runner_cls,
            patch("custom_components.tuya_cloudless.pairing_server.web.TCPSite") as mock_site_cls,
            patch(
                "custom_components.tuya_cloudless.pairing_server.register_redirect_view",
                new_callable=AsyncMock,
            ) as mock_register,
        ):
            mock_runner = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = AsyncMock()
            mock_site_cls.return_value = mock_site

            await server.start()

        mock_runner.setup.assert_awaited_once()
        mock_site.start.assert_awaited_once()
        mock_register.assert_awaited_once_with(hass, 0)

    async def test_stop_clears_runner(self) -> None:
        """stop() cleans up runner and sets it to None."""
        hass = _make_hass()
        server = PairingServer(hass, port=0)

        mock_runner = AsyncMock()
        server._runner = mock_runner

        await server.stop()

        mock_runner.cleanup.assert_awaited_once()
        assert server._runner is None
        assert server._site is None

    async def test_stop_signals_sse_queues(self) -> None:
        """stop() puts None into every open SSE queue."""
        server = PairingServer(_make_hass(), port=0)
        q1: asyncio.Queue[str | None] = asyncio.Queue()
        q2: asyncio.Queue[str | None] = asyncio.Queue()
        server._sse_queues.extend([q1, q2])

        server._runner = AsyncMock()
        await server.stop()

        assert q1.get_nowait() is None
        assert q2.get_nowait() is None
        assert server._sse_queues == []

    async def test_stop_when_runner_is_none_does_not_raise(self) -> None:
        """stop() is safe to call when the server was never started."""
        server = PairingServer(_make_hass(), port=0)
        assert server._runner is None
        await server.stop()  # must not raise


# ── ha_local_url — network-helper and internal_url fallback paths ─────────────


class TestHaLocalUrlFallbacks:
    def test_get_url_success_extracts_host(self) -> None:
        """When get_url() succeeds, ha_local_url uses that host."""
        hass = _make_hass()
        server = PairingServer(hass, port=8099)

        # get_url is lazily imported inside ha_local_url — patch at its real home
        with patch(
            "homeassistant.helpers.network.get_url",
            return_value="http://192.168.1.100:8123",
        ):
            url = server.ha_local_url()

        assert "192.168.1.100" in url
        assert ":8099" in url

    def test_get_url_raises_falls_back_to_internal_url(self) -> None:
        """When get_url() raises, internal_url is used."""
        hass = _make_hass(internal_url="http://myha.local:8123")
        server = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("unavailable"),
        ):
            url = server.ha_local_url()

        assert "myha.local" in url
        assert ":8099" in url

    def test_internal_url_empty_string_falls_through(self) -> None:
        """If internal_url is empty string, falls through to hostname fallback."""
        hass = _make_hass(internal_url="")
        server = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("unavailable"),
        ):
            url = server.ha_local_url()

        assert url.startswith("http://")
        assert ":8099" in url

    def test_both_fallbacks_fail_uses_hostname(self) -> None:
        """When both HA helpers fail, socket hostname is used."""
        hass = _make_hass(internal_url=None)
        server = PairingServer(hass, port=8099)

        with (
            patch(
                "homeassistant.helpers.network.get_url",
                side_effect=Exception("unavailable"),
            ),
            patch("socket.getfqdn", return_value="myserver.local"),
        ):
            url = server.ha_local_url()

        assert "myserver.local" in url
        assert ":8099" in url


# ── register_flow / unregister_flow ───────────────────────────────────────────


class TestFlowRegistration:
    async def test_register_flow_cancels_auto_stop_task(self) -> None:
        """register_flow cancels a pending auto-stop timer."""
        server = PairingServer(_make_hass(), port=0)
        mock_task = MagicMock()
        server._auto_stop_task = mock_task

        server.register_flow("flow-abc")

        mock_task.cancel.assert_called_once()
        assert server._auto_stop_task is None
        assert "flow-abc" in server._pending_flows

    def test_register_flow_no_task_is_noop(self) -> None:
        """register_flow with no pending task just adds the flow."""
        server = PairingServer(_make_hass(), port=0)
        server.register_flow("flow-xyz")
        assert "flow-xyz" in server._pending_flows
        assert server._auto_stop_task is None

    async def test_unregister_flow_schedules_auto_stop(self) -> None:
        """unregister_flow creates auto-stop task when no flows remain."""
        server = PairingServer(_make_hass(), port=0)
        server._pending_flows.add("flow-1")

        with patch("asyncio.ensure_future", return_value=MagicMock()) as mock_future:
            server.unregister_flow("flow-1")

        assert "flow-1" not in server._pending_flows
        mock_future.assert_called_once()
        assert server._auto_stop_task is not None

    def test_unregister_flow_with_remaining_flows_no_auto_stop(self) -> None:
        """unregister_flow does not schedule stop if other flows remain."""
        server = PairingServer(_make_hass(), port=0)
        server._pending_flows.update(["flow-1", "flow-2"])

        server.unregister_flow("flow-1")

        assert server._auto_stop_task is None

    def test_unregister_nonexistent_flow_is_safe(self) -> None:
        """Discarding an unregistered flow_id must not raise."""
        server = PairingServer(_make_hass(), port=0)
        server.unregister_flow("never-registered")


# ── _handle_index 404 path ────────────────────────────────────────────────────


class TestHandleIndex404:
    async def test_404_when_ui_dir_missing(self, client: TestClient) -> None:
        """When pairing_ui/index.html doesn't exist, returns 404."""
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            new=Path("/nonexistent/__test__"),
        ):
            server_inner = PairingServer(_make_hass(), port=0)
            ts = TestServer(server_inner._app)
            cli = TestClient(ts)
            await cli.start_server()
            try:
                resp = await cli.get("/")
                assert resp.status == 404
                text = await resp.text()
                assert "Pairing UI not found" in text
            finally:
                await cli.close()

    async def test_404_carries_security_headers(self) -> None:
        """404 index response still carries all security headers."""
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            new=Path("/nonexistent/__test__"),
        ):
            server_inner = PairingServer(_make_hass(), port=0)
            ts = TestServer(server_inner._app)
            cli = TestClient(ts)
            await cli.start_server()
            try:
                resp = await cli.get("/")
                assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
                assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            finally:
                await cli.close()


# ── _handle_config ────────────────────────────────────────────────────────────


class TestHandleConfig:
    async def test_returns_200(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        assert resp.status == 200

    async def test_response_has_activator_url(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        body = await resp.json()
        assert "activator_url" in body
        assert body["activator_url"].startswith("http://")

    async def test_response_has_events_url(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        body = await resp.json()
        assert "events_url" in body
        assert "/api/provision/events" in body["events_url"]

    async def test_response_has_result_url_template(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        body = await resp.json()
        assert "result_url_template" in body
        assert "{token}" in body["result_url_template"]

    async def test_no_cors_header_for_security(self, client: TestClient) -> None:
        """Config endpoint does NOT expose CORS wildcard (PLAT-725 security)."""
        resp = await client.get("/api/provision/config")
        assert resp.headers.get("Access-Control-Allow-Origin") is None


# ── _handle_qr ────────────────────────────────────────────────────────────────


class TestHandleQr:
    async def test_returns_503_when_qrcode_missing(self, client: TestClient) -> None:
        """Returns 503 when the qrcode library is not installed."""
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "qrcode":
                raise ImportError("qrcode not installed")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            resp = await client.get("/api/provision/qr.svg")
        assert resp.status == 503
        text = await resp.text()
        assert "qrcode" in text.lower()

    async def test_returns_svg_when_qrcode_available(self, client: TestClient) -> None:
        """Returns 200 SVG when qrcode library is present."""
        try:
            import qrcode  # noqa: F401
            import qrcode.image.svg  # noqa: F401
        except ImportError:
            pytest.skip("qrcode library not installed")

        resp = await client.get("/api/provision/qr.svg")
        assert resp.status == 200
        assert resp.content_type == "image/svg+xml"

    async def test_qr_no_cors_header_for_security(self, client: TestClient) -> None:
        """QR endpoint does NOT expose CORS wildcard (PLAT-725 security)."""
        try:
            import qrcode  # noqa: F401
        except ImportError:
            pytest.skip("qrcode library not installed")
        resp = await client.get("/api/provision/qr.svg")
        assert resp.headers.get("Access-Control-Allow-Origin") is None


# ── _handle_activate — extra field coverage ───────────────────────────────────


# Valid 32-char lowercase hex tokens (required by _TOKEN_RE since PLAT-725/825)
_TOK_CAMEL = "ca" * 16
_TOK_VER = "be" * 16
_TOK_SW = "de" * 16
_TOK_ALIAS = "fa" * 16
_TOK_PK = "ab" * 16
_TOK_NOTIFY = "dc" * 16
_TOK_NONEXISTENT = "ff" * 16


class TestActivateExtraFields:
    async def test_camel_case_gw_id(self, client: TestClient) -> None:
        """gwId (camelCase) is accepted as device identifier."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gwId": "camel_gw", "token": _TOK_CAMEL},
        )
        body = await resp.json()
        assert body["result"]["gwId"] == "camel_gw"

    async def test_sw_ver_stored_in_result(self, client: TestClient) -> None:
        """sw_ver is stored in the ActivationResult."""
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "ver_gw", "token": _TOK_VER, "sw_ver": "2.5.1"},
        )
        resp = await client.get(f"/api/provision/result/{_TOK_VER}")
        body = await resp.json()
        assert body["sw_ver"] == "2.5.1"

    async def test_swver_camel_case(self, client: TestClient) -> None:
        """swVer (camelCase) is accepted as firmware version."""
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "sw_gw", "token": _TOK_SW, "swVer": "3.0"},
        )
        resp = await client.get(f"/api/provision/result/{_TOK_SW}")
        body = await resp.json()
        assert body["sw_ver"] == "3.0"

    async def test_malformed_json_body_rejected_as_bad_request(self, client: TestClient) -> None:
        """Non-JSON body with application/json content type → 400 (empty gw_id rejected)."""
        resp = await client.post(
            "/api/tuya/device/active",
            data=b"not json at all!!!",
            headers={"Content-Type": "application/json"},
        )
        # Body parses to {} → gw_id empty → rejected with 400 (PLAT-825 validation)
        assert resp.status == 400

    async def test_no_token_result_not_stored(self, client: TestClient) -> None:
        """Activation with no token should not store a result."""
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "no_tok_gw"},
        )
        # Token not activated → result endpoint returns 202 pending (valid hex format)
        resp = await client.get(f"/api/provision/result/{_TOK_NONEXISTENT}")
        assert resp.status == 202

    async def test_api_json_alias_works(self, client: TestClient) -> None:
        """The /api.json alias endpoint processes activation the same way."""
        resp = await client.post(
            "/api.json",
            json={"gw_id": "alias_gw", "token": _TOK_ALIAS},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["result"]["gwId"] == "alias_gw"

    async def test_product_key_camel_case(self, client: TestClient) -> None:
        """productKey (camelCase) is accepted."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "pk_gw", "productKey": "MY_PRODUCT", "token": _TOK_PK},
        )
        body = await resp.json()
        assert body["result"]["gwId"] == "pk_gw"

    async def test_pending_flows_get_notified(self) -> None:
        """Pending config flows are resumed when a device activates."""
        hass = _make_hass()
        server = PairingServer(hass, port=0)
        server._pending_flows.add("flow-notify-1")

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "notified_gw", "token": _TOK_NOTIFY},
            )
            # async_create_task must have been called for the registered flow
            hass.async_create_task.assert_called()
        finally:
            await cli.close()


# ── SSE endpoint ──────────────────────────────────────────────────────────────


class TestSseEndpoint:
    async def test_sse_connection_gets_initial_keepalive(self) -> None:
        """SSE connection writes the initial ': connected\\n\\n' comment."""
        server = PairingServer(_make_hass(), port=0)
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Open SSE stream and read the first chunk only
            async with cli.session.get(
                cli.make_url("/api/provision/events")
            ) as resp:
                assert resp.status == 200
                assert resp.content_type == "text/event-stream"
                chunk = await resp.content.readany()
                assert b"connected" in chunk
        finally:
            await cli.close()

    async def test_sse_receives_activated_event(self) -> None:
        """After device activation, SSE subscribers receive an 'activated' event."""
        server = PairingServer(_make_hass(), port=0)
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            received: list[bytes] = []

            async def _consume() -> None:
                async with cli.session.get(
                    cli.make_url("/api/provision/events")
                ) as resp:
                    # Read initial comment
                    await resp.content.readany()
                    # Read the activated event
                    chunk = await asyncio.wait_for(resp.content.readany(), timeout=3.0)
                    received.append(chunk)

            task = asyncio.ensure_future(_consume())
            # Give SSE handler time to register the queue
            await asyncio.sleep(0.1)

            _tok_sse = "ee" * 16
            await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "sse_gw", "token": _tok_sse},
            )
            await asyncio.wait_for(task, timeout=5.0)

            assert len(received) == 1
            text = received[0].decode()
            assert "activated" in text
            assert "sse_gw" in text
        finally:
            await cli.close()

    async def test_broadcast_sse_puts_message_on_all_queues(self) -> None:
        """_broadcast_sse sends the formatted message to all SSE queues."""
        server = PairingServer(_make_hass(), port=0)
        q1: asyncio.Queue[str | None] = asyncio.Queue()
        q2: asyncio.Queue[str | None] = asyncio.Queue()
        server._sse_queues.extend([q1, q2])

        await server._broadcast_sse("test_event", '{"k":"v"}')

        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        assert msg1 is not None
        assert msg2 is not None
        assert "event: test_event" in msg1
        assert '{"k":"v"}' in msg1
        assert msg1 == msg2

    async def test_broadcast_sse_empty_queues_is_noop(self) -> None:
        """_broadcast_sse with no subscribers must not raise."""
        server = PairingServer(_make_hass(), port=0)
        await server._broadcast_sse("event", "data")  # no error


# ── WiFi scan extended ────────────────────────────────────────────────────────


class TestWifiScanExtended:
    async def test_nmcli_returns_ssids(self, client: TestClient) -> None:
        """When nmcli returns output, SSIDs are parsed and deduplicated."""
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"HomeNet\nOfficeNet\nHomeNet\n--\n", b""))

        async def fake_wait_for(coro: object, timeout: float) -> object:
            return await coro  # type: ignore[misc]

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
        ):
            resp = await client.get("/api/provision/wifi-scan")

        data = await resp.json()
        # Verify deduplication and -- filtering
        assert "HomeNet" in data["ssids"]
        assert "OfficeNet" in data["ssids"]
        assert data["ssids"].count("HomeNet") == 1
        assert "--" not in data["ssids"]

    async def test_timeout_returns_empty_ssids(self, client: TestClient) -> None:
        """A TimeoutError from nmcli returns an empty SSID list gracefully."""
        with patch("asyncio.create_subprocess_exec", side_effect=TimeoutError):
            resp = await client.get("/api/provision/wifi-scan")
        data = await resp.json()
        assert data["ssids"] == []

    async def test_os_error_returns_empty_ssids(self, client: TestClient) -> None:
        """An OSError from nmcli returns an empty SSID list gracefully."""
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no adapter")):
            resp = await client.get("/api/provision/wifi-scan")
        data = await resp.json()
        assert data["ssids"] == []

    async def test_wifi_scan_no_cors_header_for_security(self, client: TestClient) -> None:
        """WiFi scan endpoint does NOT expose CORS wildcard (PLAT-725 security)."""
        resp = await client.get("/api/provision/wifi-scan")
        assert resp.headers.get("Access-Control-Allow-Origin") is None


# ── _auto_stop_after_idle ─────────────────────────────────────────────────────


class TestAutoStopAfterIdle:
    async def test_auto_stop_calls_stop_pairing_server(self) -> None:
        """_auto_stop_after_idle stops the server after idle."""
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        server = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: server}}

        with (
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch(
                "custom_components.tuya_cloudless.pairing_server.stop_pairing_server",
                new_callable=AsyncMock,
            ) as mock_stop,
        ):
            await server._auto_stop_after_idle()

        mock_sleep.assert_awaited_once_with(60)
        mock_stop.assert_awaited_once_with(hass)
        assert server._auto_stop_task is None

    async def test_auto_stop_aborts_if_new_flow_registered(self) -> None:
        """_auto_stop_after_idle does nothing if flows registered while sleeping."""
        hass = _make_hass()
        server = PairingServer(hass, port=0)
        server._pending_flows.add("new-flow")

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch(
                "custom_components.tuya_cloudless.pairing_server.stop_pairing_server",
                new_callable=AsyncMock,
            ) as mock_stop,
        ):
            await server._auto_stop_after_idle()

        mock_stop.assert_not_awaited()

    async def test_auto_stop_aborts_if_server_replaced(self) -> None:
        """_auto_stop_after_idle does nothing if a different server is active."""
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        server = PairingServer(hass, port=0)
        other_server = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: other_server}}

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch(
                "custom_components.tuya_cloudless.pairing_server.stop_pairing_server",
                new_callable=AsyncMock,
            ) as mock_stop,
        ):
            await server._auto_stop_after_idle()

        mock_stop.assert_not_awaited()


# ── Module-level helpers ───────────────────────────────────────────────────────


class TestGetPairingServer:
    def test_returns_none_when_no_domain_data(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        hass.data = {}
        result = get_pairing_server(hass)
        assert result is None

    def test_returns_none_when_key_missing(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        hass.data = {DOMAIN: {}}
        result = get_pairing_server(hass)
        assert result is None

    def test_returns_server_when_present(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        server = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: server}}
        result = get_pairing_server(hass)
        assert result is server


class TestEnsurePairingServer:
    async def test_creates_server_when_none_exists(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        hass.data = {DOMAIN: {}}

        with (
            patch.object(PairingServer, "start", new_callable=AsyncMock) as mock_start,
            patch(
                "custom_components.tuya_cloudless.pairing_server.register_redirect_view",
                new_callable=AsyncMock,
            ),
        ):
            server = await ensure_pairing_server(hass)

        assert isinstance(server, PairingServer)
        mock_start.assert_awaited_once()
        assert hass.data[DOMAIN][_KEY_PAIRING_SERVER] is server

    async def test_returns_existing_server(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        existing = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: existing}}

        with patch.object(PairingServer, "start", new_callable=AsyncMock) as mock_start:
            result = await ensure_pairing_server(hass)

        mock_start.assert_not_awaited()
        assert result is existing

    async def test_idempotent_multiple_calls(self) -> None:
        """Multiple calls to ensure_pairing_server return the same instance."""
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        hass.data = {}

        with (
            patch.object(PairingServer, "start", new_callable=AsyncMock),
            patch(
                "custom_components.tuya_cloudless.pairing_server.register_redirect_view",
                new_callable=AsyncMock,
            ),
        ):
            s1 = await ensure_pairing_server(hass)
            s2 = await ensure_pairing_server(hass)

        assert s1 is s2


# ── PairingRedirectView ───────────────────────────────────────────────────────


class TestPairingRedirectView:
    def test_default_port(self) -> None:
        from custom_components.tuya_cloudless.pairing_server import PAIRING_SERVER_PORT

        view = PairingRedirectView()
        assert view._port == PAIRING_SERVER_PORT

    def test_custom_port(self) -> None:
        view = PairingRedirectView(port=9999)
        assert view._port == 9999

    async def test_get_redirects_to_correct_url(self) -> None:
        """get() raises HTTPFound pointing to http://{host}:{port}/?flow_id=..."""
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        view = PairingRedirectView(port=8099)
        request = make_mocked_request("GET", "/api/tuya_cloudless/pair/my-flow-123")
        # Override the url.host property
        request.match_info["flow_id"] = "my-flow-123"

        with patch.object(
            type(request.url),
            "host",
            new_callable=lambda: property(lambda self: "192.168.1.55"),
        ):
            with pytest.raises(web.HTTPFound) as exc_info:
                await view.get(request, "my-flow-123")

        assert "192.168.1.55" in exc_info.value.location
        assert "8099" in exc_info.value.location
        assert "my-flow-123" in exc_info.value.location

    def test_url_attribute(self) -> None:
        assert PairingRedirectView.url == "/api/tuya_cloudless/pair/{flow_id}"

    def test_name_attribute(self) -> None:
        assert PairingRedirectView.name == "api:tuya_cloudless:pair"

    def test_requires_auth_false(self) -> None:
        assert PairingRedirectView.requires_auth is False


# ── register_redirect_view ────────────────────────────────────────────────────


class TestRegisterRedirectView:
    async def test_calls_hass_register_view(self) -> None:
        from custom_components.tuya_cloudless.pairing_server import register_redirect_view

        hass = _make_hass()

        # HomeAssistantView is lazily imported inside register_redirect_view.
        # We must replace it with a plain stub class so multiple-inheritance
        # in _View(PairingRedirectView, HomeAssistantView) works correctly.
        class _FakeHAView:
            pass

        with patch(
            "homeassistant.components.http.HomeAssistantView",
            _FakeHAView,
        ):
            await register_redirect_view(hass, port=8099)

        hass.http.register_view.assert_called_once()
        registered = hass.http.register_view.call_args[0][0]
        assert registered._port == 8099

    async def test_custom_port_propagated(self) -> None:
        from custom_components.tuya_cloudless.pairing_server import register_redirect_view

        hass = _make_hass()

        class _FakeHAView:
            pass

        with patch(
            "homeassistant.components.http.HomeAssistantView",
            _FakeHAView,
        ):
            await register_redirect_view(hass, port=1234)

        registered = hass.http.register_view.call_args[0][0]
        assert registered._port == 1234


# ── stop_pairing_server ───────────────────────────────────────────────────────


class TestStopPairingServer:
    async def test_stops_running_server(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        server = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: server}}

        with patch.object(server, "stop", new_callable=AsyncMock) as mock_stop:
            await stop_pairing_server(hass)

        mock_stop.assert_awaited_once()
        assert hass.data[DOMAIN].get(_KEY_PAIRING_SERVER) is None

    async def test_noop_when_no_domain_data(self) -> None:
        hass = _make_hass()
        hass.data = {}
        await stop_pairing_server(hass)  # must not raise

    async def test_noop_when_domain_data_not_dict(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        hass.data = {DOMAIN: "not_a_dict"}
        await stop_pairing_server(hass)  # must not raise

    async def test_noop_when_server_not_in_data(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        hass.data = {DOMAIN: {}}
        await stop_pairing_server(hass)  # must not raise

    async def test_removes_server_from_domain_data(self) -> None:
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        server = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: server, "other_key": "preserved"}}

        with patch.object(server, "stop", new_callable=AsyncMock):
            await stop_pairing_server(hass)

        assert _KEY_PAIRING_SERVER not in hass.data[DOMAIN]
        assert hass.data[DOMAIN]["other_key"] == "preserved"
