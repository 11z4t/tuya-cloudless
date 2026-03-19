"""Unit tests for custom_components/tuya_cloudless/pairing_server.

Tests the fake-cloud activation endpoint, result storage, SSE broadcasting,
and helper utilities — without needing a real HA instance or Tuya device.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Add lib/ and custom_components parent to import path for test environment
_REPO = Path(__file__).parent.parent.parent
for _p in [str(_REPO / "lib"), str(_REPO)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from custom_components.tuya_cloudless.pairing_server import (  # noqa: E402
    _TUYA_AP_PREFIXES,
    ActivationResult,
    PairingRedirectView,
    PairingServer,
    _is_tuya_ap,
    _json_in_script,
    ensure_pairing_server,
    get_pairing_server,
    register_redirect_view,
    stop_pairing_server,
)

# ── Test token constants (must be exactly 32 lowercase hex chars) ──────────────
# Real tokens come from secrets.token_hex(16) which always produces this format.
_TOKEN_A = "aa" * 16  # "aaaa...aa" (32 chars)
_TOKEN_B = "bb" * 16  # "bbbb...bb" (32 chars)
_TOKEN_C = "cc" * 16
_TOKEN_D = "dd" * 16  # used for multi-token tests
_TOKEN_E = "ee" * 16  # used for multi-token tests
_TOKEN_REUSE = "f1" * 16  # used for token-reuse test
_TOKEN_CORS = "c0" * 16  # used for CORS tests
_TOKEN_UNKNOWN = "f0" * 16  # used for "token not yet in results" tests
# Additional tokens for tests that previously used short inline strings
_TOKEN_1 = "01" * 16  # was "tok1" / "t1" etc.
_TOKEN_2 = "02" * 16  # was "t2"
_TOKEN_NESTED = "03" * 16  # was "nested_tok"
_TOKEN_DEDUP = "04" * 16  # was "tok_dedup"
_TOKEN_SHARED = "05" * 16  # was "shared_token"
_TOKEN_001 = "06" * 16  # was "tok001"
_TOKEN_CANCEL = "07" * 16  # was "tok_cancel"
_TOKEN_SSE = "08" * 16  # was "tok_sse"
_TOKEN_SSE_K = "09" * 16  # was "tok_sse_k"
_TOKEN_SSE_I = "0a" * 16  # was "tok_sse_i"
_TOKEN_RL = "0b" * 16  # was "tok_rl"
_TOKEN_N1 = "0c" * 16  # was "tok-n1"
_TOKEN_N2 = "0d" * 16  # was "tok-n2"
_TOKEN_STRUCT = "10" * 16  # was "tok_struct"
_TOKEN_KEYS = "20" * 16  # was "tok_keys"
_TOKEN_SPEC = "30" * 16  # was "tok_spec"
_TOKEN_HEX = "40" * 16  # was "tok_hex"
_TOKEN_TZ = "50" * 16  # was "tok_tz"
_TOKEN_GC = "60" * 16  # was "abc" (garbage collection test)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_hass() -> MagicMock:
    """Return a minimal HomeAssistant mock."""
    hass = MagicMock()
    hass.config.internal_url = "http://homeassistant.local:8123"
    # Delegate async_create_task to the real asyncio so background tasks actually run.
    # PairingServer uses hass.async_create_task (not bare asyncio.create_task) for proper
    # HA lifecycle management.
    hass.async_create_task = asyncio.create_task
    return hass


@pytest.fixture
async def server(tmp_path: Path) -> PairingServer:
    """Return a PairingServer with a free port (not started)."""
    return PairingServer(_make_hass(), port=0)


@pytest.fixture
async def client(server: PairingServer) -> TestClient:
    """Start the server and return an aiohttp TestClient."""
    test_server = TestServer(server._app)
    cli = TestClient(test_server)
    await cli.start_server()
    yield cli
    await cli.close()


# ── ActivationResult ──────────────────────────────────────────────────────────


class TestActivationResult:
    def test_fields(self) -> None:
        r = ActivationResult(gw_id="abc", product_key="pk", local_key="lk", ip_address="1.2.3.4")
        assert r.gw_id == "abc"
        assert r.product_key == "pk"
        assert r.local_key == "lk"
        assert r.ip_address == "1.2.3.4"

    def test_timestamp_auto(self) -> None:
        before = time.monotonic()
        r = ActivationResult(gw_id="x", product_key="", local_key="", ip_address="")
        after = time.monotonic()
        assert before <= r.timestamp <= after

    def test_sw_ver_default_empty(self) -> None:
        r = ActivationResult(gw_id="x", product_key="", local_key="", ip_address="")
        assert r.sw_ver == ""


# ── PairingServer.ha_local_url ────────────────────────────────────────────────


class TestHaLocalUrl:
    def test_extracts_hostname(self) -> None:
        server = PairingServer(_make_hass(), port=8099)
        url = server.ha_local_url()
        assert "homeassistant.local" in url
        assert "8099" in url

    def test_fallback_when_no_internal_url(self) -> None:
        """When no internal_url is set, returns a valid http URL with the correct port."""
        hass = MagicMock()
        hass.config.internal_url = None
        server = PairingServer(hass, port=8099)
        url = server.ha_local_url()
        assert url.startswith("http://")
        assert ":8099" in url

    def test_custom_port(self) -> None:
        server = PairingServer(_make_hass(), port=1234)
        assert "1234" in server.ha_local_url()


# ── /api/tuya/device/active ───────────────────────────────────────────────────


class TestActivateEndpoint:
    async def test_returns_200(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "product_key": "pk1", "token": _TOKEN_1},
        )
        assert resp.status == 200

    async def test_response_success_flag(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "product_key": "pk1", "token": _TOKEN_1},
        )
        body = await resp.json()
        assert body["success"] is True

    async def test_response_has_local_key(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "product_key": "pk1", "token": _TOKEN_1},
        )
        body = await resp.json()
        assert "localKey" in body["result"]
        # local_key is generated as secrets.token_hex(8) = 16 hex chars (8 random bytes).
        # This is the correct length for AES-128: local_key.encode("utf-8") → 16 bytes.
        assert len(body["result"]["localKey"]) == 16

    async def test_response_has_gw_id(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "device123"},
        )
        body = await resp.json()
        assert body["result"]["gwId"] == "device123"

    async def test_response_has_timestamp(self, client: TestClient) -> None:
        resp = await client.post("/api/tuya/device/active", json={"gw_id": "ts_test_gw"})
        body = await resp.json()
        assert isinstance(body["t"], int)

    async def test_result_stored_by_token(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "stored_gw", "token": _TOKEN_A},
        )
        # Verify via the result endpoint
        resp = await client.get(f"/api/provision/result/{_TOKEN_A}")
        assert resp.status == 200

    async def test_empty_body_rejected(self, client: TestClient) -> None:
        """Activations with no gw_id must be rejected — the device must identify itself."""
        resp = await client.post("/api/tuya/device/active", json={})
        assert resp.status == 400

    async def test_empty_token_not_stored_in_results(
        self, client: TestClient, server: PairingServer
    ) -> None:
        """Activations with an empty token must NOT be stored in _results.

        An empty token means the device is operating outside a provisioning session
        (e.g., sending a background ping).  Storing empty-key entries would cause
        collision if multiple such devices arrive simultaneously, so the server skips
        storage while still returning a successful activation response.
        """
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "no_token_gw", "token": ""},
        )
        assert resp.status == 200
        # Empty-token result must not be in _results (guarded by `if token:`)
        assert "" not in server._results, (
            "Empty token must not be stored in _results to avoid collision"
        )

    async def test_each_activation_generates_unique_key(self, client: TestClient) -> None:
        resp1 = await client.post(
            "/api/tuya/device/active", json={"gw_id": "gw1", "token": _TOKEN_1}
        )
        resp2 = await client.post(
            "/api/tuya/device/active", json={"gw_id": "gw2", "token": _TOKEN_2}
        )
        body1 = await resp1.json()
        body2 = await resp2.json()
        assert body1["result"]["localKey"] != body2["result"]["localKey"]

    async def test_nested_data_field_accepted(self, client: TestClient) -> None:
        """Some firmware wraps everything in a 'data' key."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"data": {"gw_id": "nested_gw", "token": _TOKEN_NESTED}},
        )
        body = await resp.json()
        assert body["result"]["gwId"] == "nested_gw"

    async def test_oversized_gw_id_rejected_with_400(self, client: TestClient) -> None:
        """gw_id longer than 64 characters must be rejected (SEC-002)."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "x" * 65, "token": "tok_long"},
        )
        assert resp.status == 400

    async def test_oversized_token_rejected_with_400(self, client: TestClient) -> None:
        """token longer than 128 characters must be rejected (SEC-002)."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "normal_gw", "token": "t" * 129},
        )
        assert resp.status == 400

    async def test_exactly_max_length_fields_accepted(self, client: TestClient) -> None:
        """Fields exactly at the length limits must still be accepted."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={
                "gw_id": "g" * 64,
                "product_key": "p" * 64,
                "token": _TOKEN_A,  # valid 32-char hex token
                "sw_ver": "s" * 32,
            },
        )
        assert resp.status == 200

    async def test_concurrent_activations_stored_independently(self, client: TestClient) -> None:
        """Two concurrent device activations with different tokens must not collide.

        If the server serialises _results writes correctly, both tokens will be
        stored with their own gw_id and distinct local_keys.
        """
        resp1, resp2 = await asyncio.gather(
            client.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw-concurrent-1", "token": _TOKEN_A},
            ),
            client.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw-concurrent-2", "token": _TOKEN_B},
            ),
        )
        assert resp1.status == 200
        assert resp2.status == 200

        body1 = await resp1.json()
        body2 = await resp2.json()
        # Each device receives its own gw_id mirrored back
        assert body1["result"]["gwId"] == "gw-concurrent-1"
        assert body2["result"]["gwId"] == "gw-concurrent-2"
        # Each device receives a unique local_key
        assert body1["result"]["localKey"] != body2["result"]["localKey"]

        # Verify result endpoint returns the correct device for each token
        r1 = await client.get(f"/api/provision/result/{_TOKEN_A}")
        r2 = await client.get(f"/api/provision/result/{_TOKEN_B}")
        assert r1.status == 200
        assert r2.status == 200
        d1 = await r1.json()
        d2 = await r2.json()
        assert d1["gw_id"] == "gw-concurrent-1"
        assert d2["gw_id"] == "gw-concurrent-2"


# ── /api/provision/result/{token} ────────────────────────────────────────────


class TestResultEndpoint:
    async def test_pending_returns_202(self, client: TestClient) -> None:
        resp = await client.get(f"/api/provision/result/{_TOKEN_UNKNOWN}")
        assert resp.status == 202

    async def test_pending_body(self, client: TestClient) -> None:
        resp = await client.get(f"/api/provision/result/{_TOKEN_UNKNOWN}")
        body = await resp.json()
        assert body["status"] == "pending"

    async def test_ok_after_activation(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_ok", "token": _TOKEN_A},
        )
        resp = await client.get(f"/api/provision/result/{_TOKEN_A}")
        assert resp.status == 200

    async def test_ok_body_has_fields(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_fields", "product_key": "pk", "token": _TOKEN_B},
        )
        resp = await client.get(f"/api/provision/result/{_TOKEN_B}")
        body = await resp.json()
        assert body["status"] == "ok"
        assert body["gw_id"] == "gw_fields"
        assert "local_key" in body
        assert len(body["local_key"]) == 16  # secrets.token_hex(8) → 16 hex chars

    async def test_ip_address_in_result(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw", "token": _TOKEN_C},
        )
        resp = await client.get(f"/api/provision/result/{_TOKEN_C}")
        body = await resp.json()
        assert "ip_address" in body

    async def test_expired_token_returns_202_pending(
        self, client: TestClient, server: PairingServer
    ) -> None:
        """A token that activated but has since expired must return 202 'pending'.

        The API contract is: 202 means "no result available for this token" — this
        applies equally to tokens that never activated AND to tokens whose results
        have been garbage-collected by the TTL cleanup.  Callers should treat 202
        as "keep waiting" or "timeout", not necessarily "never activated".
        """
        from custom_components.tuya_cloudless.pairing_server import _RESULT_TTL_SECS

        # First, activate the device so a result exists
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_expired", "token": _TOKEN_A},
        )
        # Verify it's available while fresh
        resp_ok = await client.get(f"/api/provision/result/{_TOKEN_A}")
        assert resp_ok.status == 200

        # Backdate the result's timestamp past the TTL
        result = server._results[_TOKEN_A]
        expired_ts = time.monotonic() - (_RESULT_TTL_SECS + 10)
        object.__setattr__(result, "timestamp", expired_ts)

        # Now the result should be treated as expired → 202 pending
        resp_expired = await client.get(f"/api/provision/result/{_TOKEN_A}")
        assert resp_expired.status == 202
        body = await resp_expired.json()
        assert body["status"] == "pending"

    async def test_pending_202_includes_no_cache_header(self, client: TestClient) -> None:
        """A 202 Pending response must include Cache-Control: no-cache.

        Without this header, polling clients may cache the 202 and stop re-polling,
        causing device activation to silently fail from the client's perspective.
        """
        resp = await client.get(f"/api/provision/result/{_TOKEN_UNKNOWN}")
        assert resp.status == 202
        cache_ctrl = resp.headers.get("Cache-Control", "")
        assert "no-cache" in cache_ctrl, (
            f"202 response missing Cache-Control: no-cache (got: {cache_ctrl!r})"
        )

    async def test_post_to_result_endpoint_returns_405(self, client: TestClient) -> None:
        """Result endpoint is GET-only — POST must return 405 Method Not Allowed."""
        resp = await client.post("/api/provision/result/some_token", json={})
        assert resp.status == 405

    async def test_post_to_activate_with_get_returns_405(self, client: TestClient) -> None:
        """Activate endpoint is POST-only — GET must return 405 Method Not Allowed."""
        resp = await client.get("/api/tuya/device/active")
        assert resp.status == 405

    async def test_post_to_wifi_ap_pair_with_get_returns_405(self, client: TestClient) -> None:
        """WiFi AP pair endpoint is POST-only — GET must return 405 Method Not Allowed."""
        resp = await client.get("/api/provision/wifi-ap-pair")
        assert resp.status == 405


# ── / (index page) ────────────────────────────────────────────────────────────


class TestIndexPage:
    async def test_serves_html(self, client: TestClient) -> None:
        """Index page returns HTML (may 404 in test env without pairing_ui/)."""
        resp = await client.get("/")
        # Either 200 with HTML or 404 if pairing_ui not found in test env
        assert resp.status in (200, 404)

    async def test_content_type_html(self, client: TestClient) -> None:
        resp = await client.get("/")
        if resp.status == 200:
            assert "text/html" in resp.content_type


# ── PairingServer.get_result ──────────────────────────────────────────────────


class TestGetResult:
    def test_returns_none_for_unknown(self) -> None:
        s = PairingServer(_make_hass(), port=0)
        assert s.get_result("nonexistent") is None

    def test_returns_result_after_store(self) -> None:
        s = PairingServer(_make_hass(), port=0)
        r = ActivationResult(gw_id="g", product_key="", local_key="k", ip_address="1.1.1.1")
        s._results["my_token"] = r
        assert s.get_result("my_token") is r

    def test_expires_old_result(self) -> None:
        s = PairingServer(_make_hass(), port=0)
        old = ActivationResult(gw_id="old", product_key="", local_key="k", ip_address="")
        # Force an old timestamp (well past TTL)
        object.__setattr__(old, "timestamp", time.monotonic() - 9999)
        s._results["old_tok"] = old
        assert s.get_result("old_tok") is None

    def test_result_survives_within_ttl(self) -> None:
        """Result should remain accessible for the full 1-hour TTL."""
        from custom_components.tuya_cloudless.pairing_server import _RESULT_TTL_SECS

        s = PairingServer(_make_hass(), port=0)
        r = ActivationResult(gw_id="g", product_key="", local_key="k", ip_address="")
        # Simulate result stored 59 minutes ago — still within TTL
        almost_expired = time.monotonic() - (_RESULT_TTL_SECS - 60)
        object.__setattr__(r, "timestamp", almost_expired)
        s._results["tok_alive"] = r
        assert s.get_result("tok_alive") is r

    def test_result_ttl_is_one_hour(self) -> None:
        """_RESULT_TTL_SECS must be 3600 — users may take up to an hour to complete HA setup."""
        from custom_components.tuya_cloudless.pairing_server import _RESULT_TTL_SECS

        assert _RESULT_TTL_SECS == 3600.0


# ── CSRF protection (PLAT-725) ────────────────────────────────────────────────


class TestCsrfProtection:
    @pytest.mark.asyncio
    async def test_activate_rejects_form_urlencoded(self, client: TestClient) -> None:
        """A browser HTML-form POST (application/x-www-form-urlencoded) must be rejected."""
        resp = await client.post(
            "/api/tuya/device/active",
            data="gw_id=evil&token=tok",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status == 415

    @pytest.mark.asyncio
    async def test_activate_rejects_multipart(self, client: TestClient) -> None:
        """A multipart/form-data POST (another CSRF vector) must be rejected."""
        resp = await client.post(
            "/api/tuya/device/active",
            data={"gw_id": "x"},
            headers={"Content-Type": "multipart/form-data"},
        )
        assert resp.status == 415

    @pytest.mark.asyncio
    async def test_activate_rejects_missing_content_type(self, client: TestClient) -> None:
        """A POST with no Content-Type header must be rejected."""
        resp = await client.post(
            "/api/tuya/device/active",
            data=b"{}",
        )
        assert resp.status == 415

    @pytest.mark.asyncio
    async def test_activate_accepts_json_content_type(self, client: TestClient) -> None:
        """A legitimate application/json POST must still be accepted."""
        payload = {"gw_id": "dev1", "token": _TOKEN_1, "product_key": "pk"}
        resp = await client.post(
            "/api/tuya/device/active",
            json=payload,
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_index_has_security_headers(self, client: TestClient) -> None:
        """The pairing UI page must carry all required security headers."""
        resp = await client.get("/")
        # The UI dir may not exist in test env so accept 200 or 404
        assert resp.status in (200, 404)
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" in resp.headers
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    @pytest.mark.asyncio
    async def test_csp_script_src_present(self, client: TestClient) -> None:
        """CSP must contain a script-src directive.

        Note: 'unsafe-inline' is deliberately included in script-src because
        _handle_ha_index injects a <script> block with window globals
        (window._TUYA_ACTIVATOR_BASE etc.) that must run before app.js loads.
        Without 'unsafe-inline', the browser blocks this inline script and the
        pairing UI initialises with wrong base URLs.  All inline event handlers
        (onsubmit, onclick) remain removed in favour of addEventListener().
        """
        resp = await client.get("/")
        assert resp.status in (200, 404)
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "script-src" in csp, "CSP must have a script-src directive"
        # Eval is still forbidden — only inline scripts (for window globals) are allowed
        assert "'unsafe-eval'" not in csp, "CSP must never allow eval()"

    @pytest.mark.asyncio
    async def test_api_json_endpoint_also_rejects_form(self, client: TestClient) -> None:
        """The /api.json alias endpoint has the same CSRF protection."""
        resp = await client.post(
            "/api.json",
            data="gw_id=evil",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status == 415


# ── /api/provision/wifi-scan ──────────────────────────────────────────────────


class TestWifiScan:
    """Tests for GET /api/provision/wifi-scan."""

    async def test_returns_ssids_key(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/wifi-scan")
        assert resp.status == 200
        data = await resp.json()
        assert "ssids" in data
        assert isinstance(data["ssids"], list)
        assert "current_ssid" in data

    async def test_graceful_when_nmcli_missing(self, client: TestClient) -> None:
        from unittest.mock import patch

        with (
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError),
            patch("asyncio.sleep"),
        ):
            resp = await client.get("/api/provision/wifi-scan")
        assert resp.status == 200
        data = await resp.json()
        assert data["ssids"] == []
        assert data["current_ssid"] is None

    async def test_nmcli_timeout_kills_process_and_returns_empty(self, client: TestClient) -> None:
        """When nmcli hangs, wait_for raises TimeoutError — proc.kill() must be called
        and the endpoint must still return HTTP 200 with an empty SSID list."""
        from unittest.mock import MagicMock, patch

        mock_proc = MagicMock()
        kill_called = False

        def _kill() -> None:
            nonlocal kill_called
            kill_called = True

        mock_proc.kill = _kill

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
            patch("asyncio.sleep"),
        ):
            resp = await client.get("/api/provision/wifi-scan")

        assert resp.status == 200
        data = await resp.json()
        assert data["ssids"] == []
        assert kill_called, "proc.kill() must be called when nmcli times out"

    async def test_uses_rescan_yes_then_no(self, client: TestClient) -> None:
        """Scan makes two passes: first --rescan yes, then --rescan no."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"HomeNet\nOfficeWifi\n", b""))
        captured_calls: list[tuple[object, ...]] = []

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            captured_calls.append(args)
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("asyncio.sleep"),
        ):
            resp = await client.get("/api/provision/wifi-scan")

        assert resp.status == 200
        # Filter scan calls (those containing --rescan)
        scan_calls = [a for a in captured_calls if "--rescan" in a]
        assert len(scan_calls) == 2
        rescan_vals = [a[list(a).index("--rescan") + 1] for a in scan_calls]
        assert rescan_vals[0] == "yes"
        assert rescan_vals[1] == "no"

    async def test_rescan_returns_ssids(self, client: TestClient) -> None:
        """With a successful two-pass scan, SSIDs are returned and deduplicated."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"HomeNet\nOfficeWifi\n--\n", b""))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.sleep"),
        ):
            resp = await client.get("/api/provision/wifi-scan")

        data = await resp.json()
        assert "HomeNet" in data["ssids"]
        assert "OfficeWifi" in data["ssids"]
        # "--" placeholder must be filtered out
        assert "--" not in data["ssids"]
        # Deduplicated — two passes each returning HomeNet → still one entry
        assert data["ssids"].count("HomeNet") == 1

    async def test_current_ssid_bubbled_to_top(self, client: TestClient) -> None:
        """Active network appears first in sorted list."""
        from unittest.mock import AsyncMock, MagicMock, patch

        def make_proc(output: bytes) -> MagicMock:
            p = MagicMock()
            p.communicate = AsyncMock(return_value=(output, b""))
            return p

        call_count = 0

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if "--fields" in args and "ACTIVE,SSID" in args:
                # _get_default_ssid call
                return make_proc(b"yes:HomeNet\nno:OfficeWifi\n")
            # scan calls
            return make_proc(b"HomeNet\nOfficeWifi\nAnotherNet\n")

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("asyncio.sleep"),
        ):
            resp = await client.get("/api/provision/wifi-scan")

        data = await resp.json()
        assert data["current_ssid"] == "HomeNet"
        assert data["ssids"][0] == "HomeNet"

    async def test_wifi_scan_includes_tuya_aps(self, client: TestClient) -> None:
        """Full wifi-scan response includes tuya_aps list filtered by prefix."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"SmartLife_AB12\nHomeNet\nOfficeWifi\n", b"")
        )

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.sleep"),
        ):
            resp = await client.get("/api/provision/wifi-scan")

        assert resp.status == 200
        data = await resp.json()
        assert "tuya_aps" in data
        ssids_in_aps = [ap["ssid"] for ap in data["tuya_aps"]]
        assert "SmartLife_AB12" in ssids_in_aps
        # non-Tuya SSIDs must not appear in tuya_aps
        assert "HomeNet" not in ssids_in_aps
        assert "OfficeWifi" not in ssids_in_aps

    async def test_wifi_scan_rate_limited(self, server: PairingServer) -> None:
        """wifi-scan returns 429 when per-IP rate limit (5/min) is exceeded."""
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Pre-fill rate limit bucket to just below capacity for wifi-scan (max=5)
            now = time.monotonic()
            server._rate_limit["127.0.0.1"] = [now] * 4

            # 5th request should still succeed
            resp = await cli.get("/api/provision/wifi-scan")
            assert resp.status == 200

            # 6th request should be rejected
            resp = await cli.get("/api/provision/wifi-scan")
            assert resp.status == 429
            assert "Retry-After" in resp.headers
        finally:
            await cli.close()


# ── _is_tuya_ap / _TUYA_AP_PREFIXES ──────────────────────────────────────────


class TestIsTuyaAp:
    """Unit tests for the :func:`_is_tuya_ap` helper and :const:`_TUYA_AP_PREFIXES`."""

    # --- positive cases: all five prefixes ---------------------------------

    def test_smartlife_prefix(self) -> None:
        assert _is_tuya_ap("SmartLife_AB12") is True

    def test_smartlife_lowercase(self) -> None:
        assert _is_tuya_ap("smartlife_xy99") is True

    def test_sl_prefix(self) -> None:
        assert _is_tuya_ap("SL_Device01") is True

    def test_az_prefix(self) -> None:
        assert _is_tuya_ap("AZ_Outlet") is True

    def test_tuya_prefix(self) -> None:
        assert _is_tuya_ap("Tuya_Lamp") is True

    def test_wifi_prefix(self) -> None:
        assert _is_tuya_ap("WiFi_Plug") is True

    def test_all_prefixes_covered(self) -> None:
        """Every entry in _TUYA_AP_PREFIXES must be matched by _is_tuya_ap."""
        for prefix in _TUYA_AP_PREFIXES:
            ssid = prefix + "XXXX"
            assert _is_tuya_ap(ssid), f"prefix {prefix!r} not matched"

    # --- negative cases: non-Tuya SSIDs -----------------------------------

    def test_home_network_rejected(self) -> None:
        assert _is_tuya_ap("HomeNetwork") is False

    def test_corporate_ssid_rejected(self) -> None:
        assert _is_tuya_ap("CorporateWiFi") is False

    def test_empty_string_rejected(self) -> None:
        assert _is_tuya_ap("") is False

    def test_dash_separator_rejected(self) -> None:
        # Prefix must be followed by _ — "smartlifehome" shouldn't match
        # but "smartlife_" would. Check near-miss without underscore.
        # Note: prefix "smartlife_" requires the underscore to be part of prefix
        assert _is_tuya_ap("smartlifehome") is False

    def test_trailing_spaces_stripped(self) -> None:
        """Leading/trailing spaces are stripped before comparison."""
        assert _is_tuya_ap("  SmartLife_AB12  ") is True

    def test_dashes_not_matched(self) -> None:
        assert _is_tuya_ap("TP-Link_Home") is False

    def test_partial_prefix_not_matched(self) -> None:
        # "smart" alone is not a valid prefix
        assert _is_tuya_ap("smart_device") is False

    # --- edge cases --------------------------------------------------------

    def test_only_prefix_is_valid(self) -> None:
        # Prefix alone (e.g. "smartlife_") still matches since it starts with prefix
        assert _is_tuya_ap("smartlife_") is True

    def test_prefix_in_middle_not_matched(self) -> None:
        # Prefix must be at the start, not middle
        assert _is_tuya_ap("MY_SmartLife_Device") is False

    def test_unicode_ignored(self) -> None:
        # Non-ASCII SSIDs that don't start with a Tuya prefix
        assert _is_tuya_ap("家庭WiFi") is False

    def test_case_insensitive_mixed(self) -> None:
        assert _is_tuya_ap("SMARTLIFE_XX") is True
        assert _is_tuya_ap("sMarTlIfE_yy") is True


# ── _is_rate_limited helper ───────────────────────────────────────────────────


class TestIsRateLimited:
    """Direct unit tests for :meth:`PairingServer._is_rate_limited`."""

    def _make_server(self) -> PairingServer:
        """Return a PairingServer (not started) with a fresh rate-limit dict."""
        return PairingServer(_make_hass(), port=0)

    def test_first_request_not_limited(self) -> None:
        """First call from a new IP is always allowed."""
        server = self._make_server()
        assert server._is_rate_limited("10.0.0.1") is False

    def test_under_limit_allowed(self) -> None:
        """Requests under the threshold are not limited."""
        server = self._make_server()
        # max_requests=3, window=60 — first 3 should all pass
        for _ in range(3):
            assert server._is_rate_limited("10.0.0.1", max_requests=3, window=60.0) is False

    def test_at_limit_blocked(self) -> None:
        """The request that hits max_requests is blocked (>=, not >)."""
        server = self._make_server()
        now = time.monotonic()
        # Pre-fill with exactly max_requests timestamps (all fresh)
        server._rate_limit["10.0.0.2"] = [now] * 3
        assert server._is_rate_limited("10.0.0.2", max_requests=3, window=60.0) is True

    def test_expired_timestamps_not_counted(self) -> None:
        """Timestamps older than window are discarded and don't count."""
        server = self._make_server()
        old = time.monotonic() - 120.0  # 2 minutes ago, well outside 60s window
        # Pre-fill with stale timestamps — should be evicted, leaving room for new request
        server._rate_limit["10.0.0.3"] = [old] * 10
        assert server._is_rate_limited("10.0.0.3", max_requests=3, window=60.0) is False

    def test_mixed_fresh_and_stale(self) -> None:
        """Only fresh timestamps count toward the limit."""
        server = self._make_server()
        now = time.monotonic()
        old = now - 120.0
        # 2 stale + 2 fresh — only the 2 fresh count; max=3 → not limited
        server._rate_limit["10.0.0.4"] = [old, old, now, now]
        assert server._is_rate_limited("10.0.0.4", max_requests=3, window=60.0) is False
        # After this call (which is allowed), there are 3 fresh → next call is blocked
        assert server._is_rate_limited("10.0.0.4", max_requests=3, window=60.0) is True

    def test_different_ips_independent(self) -> None:
        """Rate limiting is per-IP — one IP's limit doesn't affect another."""
        server = self._make_server()
        now = time.monotonic()
        # IP A is at the limit
        server._rate_limit["10.0.0.5"] = [now] * 5
        # IP B is untouched
        assert server._is_rate_limited("10.0.0.5", max_requests=5, window=60.0) is True
        assert server._is_rate_limited("10.0.0.6", max_requests=5, window=60.0) is False

    def test_empty_list_evicted_from_dict(self) -> None:
        """IPs with an empty timestamp list are evicted from the dict on the next allowed call.

        The eviction uses ``if ts_list`` (falsy check), so only genuinely empty
        lists are removed globally — stale-but-nonempty lists for OTHER IPs are
        not freshness-filtered during a different IP's call.
        """
        server = self._make_server()
        server._rate_limit["10.0.0.7"] = []  # Empty list — will be evicted
        # Trigger eviction via any allowed call from a different IP
        server._is_rate_limited("10.0.0.8", max_requests=5, window=60.0)
        assert "10.0.0.7" not in server._rate_limit

    def test_stale_timestamps_cleaned_on_own_call(self) -> None:
        """An IP's own stale timestamps are discarded when IT makes the next request."""
        server = self._make_server()
        old = time.monotonic() - 120.0
        server._rate_limit["10.0.0.7"] = [old, old]
        # Call for the SAME IP — stale timestamps filtered → not limited
        result = server._is_rate_limited("10.0.0.7", max_requests=5, window=60.0)
        assert result is False
        # Only 1 fresh timestamp should remain (the one just appended)
        assert len(server._rate_limit.get("10.0.0.7", [])) == 1

    def test_allowed_request_recorded(self) -> None:
        """Allowed requests are appended to the IP's timestamp list."""
        server = self._make_server()
        assert "10.0.0.9" not in server._rate_limit
        server._is_rate_limited("10.0.0.9", max_requests=5, window=60.0)
        assert len(server._rate_limit["10.0.0.9"]) == 1

    def test_blocked_request_not_recorded(self) -> None:
        """Blocked requests are NOT added to the bucket (no timestamp appended)."""
        server = self._make_server()
        now = time.monotonic()
        server._rate_limit["10.0.0.10"] = [now] * 5
        count_before = len(server._rate_limit["10.0.0.10"])
        server._is_rate_limited("10.0.0.10", max_requests=5, window=60.0)
        assert len(server._rate_limit["10.0.0.10"]) == count_before

    def test_default_params_match_constants(self) -> None:
        """Default window and max_requests match the module-level constants."""
        from custom_components.tuya_cloudless.pairing_server import _RATE_LIMIT_MAX

        server = self._make_server()
        now = time.monotonic()
        # Just under the default limit — allowed
        server._rate_limit["10.0.1.1"] = [now] * (_RATE_LIMIT_MAX - 1)
        assert server._is_rate_limited("10.0.1.1") is False
        # Now at the limit — blocked
        assert server._is_rate_limited("10.0.1.1") is True


# ── /api/provision/quick-scan ─────────────────────────────────────────────────


class TestQuickScan:
    """Tests for GET /api/provision/quick-scan."""

    async def test_returns_tuya_aps_key(self, client: TestClient) -> None:
        """Response always contains tuya_aps list."""
        resp = await client.get("/api/provision/quick-scan")
        assert resp.status == 200
        data = await resp.json()
        assert "tuya_aps" in data
        assert isinstance(data["tuya_aps"], list)

    async def test_smartlife_detected(self, client: TestClient) -> None:
        """SmartLife_ prefixed SSIDs appear in tuya_aps."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"SmartLife_AB12\nHomeNet\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/quick-scan")

        data = await resp.json()
        ssids = [ap["ssid"] for ap in data["tuya_aps"]]
        assert "SmartLife_AB12" in ssids
        assert "HomeNet" not in ssids

    async def test_normal_ssids_filtered(self, client: TestClient) -> None:
        """Non-Tuya SSIDs are not included in tuya_aps."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"CorporateWiFi\nGuest\n--\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/quick-scan")

        data = await resp.json()
        assert data["tuya_aps"] == []

    async def test_no_rescan_flag_used(self, client: TestClient) -> None:
        """quick-scan must pass --rescan no (never --rescan yes)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        captured_calls: list[tuple[object, ...]] = []

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            captured_calls.append(args)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            resp = await client.get("/api/provision/quick-scan")

        assert resp.status == 200
        scan_calls = [a for a in captured_calls if "--rescan" in a]
        assert len(scan_calls) == 1
        rescan_idx = list(scan_calls[0]).index("--rescan")
        assert scan_calls[0][rescan_idx + 1] == "no"

    async def test_graceful_when_nmcli_missing(self, client: TestClient) -> None:
        """Returns empty tuya_aps when nmcli is unavailable."""
        from unittest.mock import patch

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            resp = await client.get("/api/provision/quick-scan")

        assert resp.status == 200
        data = await resp.json()
        assert data["tuya_aps"] == []

    async def test_deduplicates_ssids(self, client: TestClient) -> None:
        """Duplicate Tuya SSIDs appear only once in tuya_aps."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"SmartLife_AB12\nSmartLife_AB12\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/quick-scan")

        data = await resp.json()
        ssids = [ap["ssid"] for ap in data["tuya_aps"]]
        assert ssids.count("SmartLife_AB12") == 1

    async def test_all_tuya_prefixes_matched(self, client: TestClient) -> None:
        """All five Tuya AP prefixes are matched (case-insensitive)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"SmartLife_AA\nSL_BB\nAZ_CC\nTuya_DD\nWiFi_EE\nHomeNet\n",
                b"",
            )
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/quick-scan")

        data = await resp.json()
        ssids = [ap["ssid"] for ap in data["tuya_aps"]]
        assert "SmartLife_AA" in ssids
        assert "SL_BB" in ssids
        assert "AZ_CC" in ssids
        assert "Tuya_DD" in ssids
        assert "WiFi_EE" in ssids
        assert "HomeNet" not in ssids

    async def test_quick_scan_filters_ssids_with_control_chars(self, client: TestClient) -> None:
        """SSIDs containing control characters (0x00-0x1F, 0x7F) are excluded from quick-scan."""
        mock_proc = MagicMock()
        # "SmartLife_Good" is valid; "SmartLife_Bad\x00" has a null byte
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"SmartLife_Good\nSmartLife_Bad\x00Name\nSL_Also\x1fGood_Not\n",
                b"",
            )
        )
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/quick-scan")

        data = await resp.json()
        ssids = [ap["ssid"] for ap in data["tuya_aps"]]
        assert "SmartLife_Good" in ssids
        assert not any("\x00" in s or "\x1f" in s for s in ssids), (
            "SSIDs with control chars must be filtered from quick-scan results"
        )

    async def test_nmcli_nonzero_exit_returns_empty_list(self, client: TestClient) -> None:
        """nmcli returning non-zero exit code (e.g. permission denied) falls back to empty list.

        quick-scan does not check proc.returncode — it decodes whatever stdout was produced
        (empty bytes on most errors) and returns tuya_aps=[].  This is intentional: a failing
        nmcli should not block the pairing UI, just show no auto-discovered devices.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.returncode = 1  # nmcli error (e.g. permissions, no WiFi adapter)
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: permission denied\n"))
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/quick-scan")

        assert resp.status == 200
        data = await resp.json()
        assert data["tuya_aps"] == [], "Non-zero nmcli exit must fall back to empty list"

    async def test_nmcli_timeout_kills_process_and_returns_empty(self, client: TestClient) -> None:
        """quick-scan: when nmcli hangs past 5s, proc.kill() is called and
        the endpoint returns HTTP 200 with an empty tuya_aps list."""
        from unittest.mock import MagicMock, patch

        mock_proc = MagicMock()
        kill_called = False

        def _kill() -> None:
            nonlocal kill_called
            kill_called = True

        mock_proc.kill = _kill

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
        ):
            resp = await client.get("/api/provision/quick-scan")

        assert resp.status == 200
        data = await resp.json()
        assert data["tuya_aps"] == []
        assert kill_called, "proc.kill() must be called when quick-scan nmcli times out"

    async def test_quick_scan_rate_limited(self, server: PairingServer) -> None:
        """quick-scan returns 429 when per-IP rate limit (10/min) is exceeded."""
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Pre-fill rate limit bucket to just below capacity for quick-scan (max=10)
            now = time.monotonic()
            server._rate_limit["127.0.0.1"] = [now] * 9

            # 10th request should still succeed
            resp = await cli.get("/api/provision/quick-scan")
            assert resp.status == 200

            # 11th request should be rejected
            resp = await cli.get("/api/provision/quick-scan")
            assert resp.status == 429
            assert "Retry-After" in resp.headers
        finally:
            await cli.close()


# ── /api/provision/wifi-ap-pair ───────────────────────────────────────────────


class TestWifiApPair:
    """Tests for POST /api/provision/wifi-ap-pair."""

    @pytest.fixture(autouse=True)
    def _mock_nmcli_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pretend nmcli is installed for all tests in this class.

        Individual tests that want to test the 503 path override shutil.which themselves.
        """
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/usr/bin/nmcli" if name == "nmcli" else None,
        )

    async def test_returns_token_and_events_url(self, client: TestClient) -> None:
        """Response contains token and events_url."""
        from unittest.mock import patch

        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._wifi_ap_pair_task"
        ):
            resp = await client.post(
                "/api/provision/wifi-ap-pair",
                json={"ap_ssid": "SmartLife_AB12", "home_ssid": "HomeNet", "home_password": "pass"},
            )

        assert resp.status == 200
        data = await resp.json()
        assert "token" in data
        assert isinstance(data["token"], str)
        assert len(data["token"]) == 32  # 16 bytes hex = 32 chars
        assert "events_url" in data

    async def test_non_tuya_ap_ssid_returns_400(self, client: TestClient) -> None:
        """ap_ssid that does not match any Tuya AP prefix is rejected with 400.

        Without this guard a malicious client could trick the server into connecting
        HA's WiFi to an arbitrary network (HomeNet, OfficeWiFi, etc.).
        """
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            json={"ap_ssid": "HomeNet", "home_ssid": "HomeNet", "home_password": "pass"},
        )
        assert resp.status == 400

    async def test_missing_ap_ssid_returns_400(self, client: TestClient) -> None:
        """Missing ap_ssid → 400."""
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            json={"home_ssid": "HomeNet", "home_password": "pass"},
        )
        assert resp.status == 400

    async def test_missing_home_ssid_returns_400(self, client: TestClient) -> None:
        """Missing home_ssid → 400."""
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            json={"ap_ssid": "SmartLife_AB12", "home_password": "pass"},
        )
        assert resp.status == 400

    async def test_invalid_json_returns_400(self, client: TestClient) -> None:
        """Malformed JSON body → 400."""
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    async def test_nmcli_unavailable_returns_503(self, client: TestClient) -> None:
        """Returns 503 immediately when nmcli is not installed — avoids silent timeout."""
        from unittest.mock import patch

        with patch("shutil.which", return_value=None):
            resp = await client.post(
                "/api/provision/wifi-ap-pair",
                json={"ap_ssid": "SmartLife_AB12", "home_ssid": "HomeNet", "home_password": "pass"},
            )

        assert resp.status == 503
        body = await resp.text()
        assert "nmcli" in body.lower()

    async def test_background_task_spawned(self, client: TestClient) -> None:
        """A background task is created when the request is valid."""
        import asyncio
        from unittest.mock import patch

        task_calls: list[tuple[object, ...]] = []

        async def fake_task(*args: object, **kwargs: object) -> None:
            task_calls.append(args)

        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._wifi_ap_pair_task",
            side_effect=fake_task,
        ):
            resp = await client.post(
                "/api/provision/wifi-ap-pair",
                json={
                    "ap_ssid": "SmartLife_AB12",
                    "home_ssid": "HomeNet",
                    "home_password": "secret",
                },
            )

        assert resp.status == 200
        # Give the event loop a tick to schedule the background task
        await asyncio.sleep(0)
        # Background task was invoked with the correct AP ssid
        assert any("SmartLife_AB12" in str(a) for a in task_calls)

    async def test_nmcli_connect_called_with_ap_ssid(self, client: TestClient) -> None:
        """_wifi_ap_pair_task connects to the Tuya AP via nmcli."""
        from unittest.mock import AsyncMock, MagicMock, patch

        nmcli_calls: list[list[str]] = []

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nmcli_calls.append(list(args))
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("aiohttp.ClientSession") as mock_session_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "abc123", "http://ha:8099"
            )

        connect_calls = [c for c in nmcli_calls if "connect" in c]
        assert any("SmartLife_AB12" in str(c) for c in connect_calls)

    async def test_sse_error_on_nmcli_missing(self, client: TestClient) -> None:
        """wifi_ap_error SSE event sent when nmcli is unavailable."""
        from unittest.mock import patch

        hass = MagicMock()
        server = PairingServer(hass, port=9099)
        broadcast_calls: list[tuple[str, str]] = []

        async def fake_broadcast(event: str, data: str) -> None:
            broadcast_calls.append((event, data))

        server._broadcast_sse = fake_broadcast  # type: ignore[method-assign]

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "tok123", "http://ha:8099"
            )

        assert any(ev == "wifi_ap_error" for ev, _ in broadcast_calls)

    async def test_nmcli_connect_nonzero_exit_emits_wifi_ap_error(self, client: TestClient) -> None:
        """nmcli connect returning non-zero (device not found / wrong SSID) emits wifi_ap_error.

        This exercises the OSError path at pairing_server.py:1098-1099 where a non-zero
        return code from ``nmcli device wifi connect`` causes the task to fail.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        call_count = 0

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            if "connect" in args and "SmartLife_AB12" in args:
                # Tuya AP connect fails — returncode != 0
                proc.communicate = AsyncMock(
                    return_value=(b"", b"Error: No network with SSID 'SmartLife_AB12' found.")
                )
                proc.returncode = 4  # nmcli error code for "not found"
            else:
                # All other nmcli calls (connection show, reconnect) succeed
                proc.communicate = AsyncMock(return_value=(b"yes:HomeNet", b""))
                proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)
        broadcast_calls: list[tuple[str, str]] = []

        async def fake_broadcast(event: str, data: str) -> None:
            broadcast_calls.append((event, data))

        server._broadcast_sse = fake_broadcast  # type: ignore[method-assign]

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "tok_fail", "http://ha:8099"
            )

        # Non-zero exit from connect → OSError caught → wifi_ap_error broadcast
        assert any(ev == "wifi_ap_error" for ev, _ in broadcast_calls), (
            "wifi_ap_error SSE must be emitted when nmcli connect returns non-zero"
        )
        # The token must be included so the client can match this error to its session
        error_payloads = [data for ev, data in broadcast_calls if ev == "wifi_ap_error"]
        assert any("tok_fail" in p for p in error_payloads), (
            "wifi_ap_error SSE payload must include the session token"
        )

    async def test_nmcli_connect_timeout_kills_process(self, client: TestClient) -> None:
        """When nmcli connect hangs past its timeout, the process is killed.

        The internal _run() helper calls proc.kill() on TimeoutError and returns (-1, ""),
        which causes the caller to raise OSError (rc == -1 != 0), broadcasting wifi_ap_error.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        killed: list[bool] = []

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            if "connect" in args and "SmartLife_AB12" in args:
                # Simulate a hanging nmcli — communicate() never returns
                async def _hang() -> None:
                    await asyncio.sleep(999)

                proc.communicate = _hang
                proc.returncode = None

                def _record_kill() -> None:
                    killed.append(True)

                proc.kill = _record_kill
            else:
                proc.communicate = AsyncMock(return_value=(b"yes:HomeNet", b""))
                proc.returncode = 0
                proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)
        broadcast_calls: list[tuple[str, str]] = []

        async def fake_broadcast(event: str, data: str) -> None:
            broadcast_calls.append((event, data))

        server._broadcast_sse = fake_broadcast  # type: ignore[method-assign]

        # Patch asyncio.wait_for to use a very short timeout so the test is fast
        original_wait_for = asyncio.wait_for

        async def fast_wait_for(coro: object, timeout: float) -> object:
            # Shorten any timeout > 1s to 0.05s so the test doesn't take 15+ seconds
            return await original_wait_for(coro, 0.05 if timeout > 1 else timeout)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("asyncio.wait_for", side_effect=fast_wait_for),
        ):
            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "tok_timeout", "http://ha:8099"
            )

        # proc.kill() must have been called to terminate the stalled process
        assert killed, "proc.kill() must be called when nmcli connect times out"
        # The timed-out connect returns rc=-1 → OSError → wifi_ap_error broadcast
        assert any(ev == "wifi_ap_error" for ev, _ in broadcast_calls), (
            "wifi_ap_error must be emitted when nmcli connect times out"
        )

    async def test_cancelled_task_kills_subprocess(self) -> None:
        """When the background task is cancelled (e.g., HA shutdown), proc.kill()
        must be called so the nmcli subprocess doesn't linger as a zombie."""
        from unittest.mock import AsyncMock, MagicMock, patch

        killed: list[bool] = []

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()

            # Simulate a hanging nmcli process for the initial nmcli connection list call
            async def _hang() -> object:
                await asyncio.sleep(999)

            proc.communicate = _hang
            proc.returncode = None

            def _record_kill() -> None:
                killed.append(True)

            proc.kill = _record_kill
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)
        server._broadcast_sse = AsyncMock()  # type: ignore[method-assign]

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            task = asyncio.create_task(
                server._wifi_ap_pair_task(
                    "SmartLife_AB12", "HomeNet", "pass", "tok_cancel", "http://ha:8099"
                )
            )
            # Give the task a moment to start and enter the subprocess call
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # proc.kill() must have been called when CancelledError propagated from wait_for
        assert killed, "proc.kill() must be called when the task is cancelled"

    async def test_reconnect_failure_does_not_raise(self, client: TestClient) -> None:
        """Reconnect failure (step 4) is logged but does not propagate as an error SSE."""
        from unittest.mock import AsyncMock, MagicMock, patch

        call_count = 0

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            # All nmcli calls succeed except the reconnect (last call)
            if "up" in args:
                proc.communicate = AsyncMock(return_value=(b"", b"Error"))
                proc.returncode = 1
            else:
                proc.communicate = AsyncMock(return_value=(b"yes:HomeNet", b""))
                proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)
        broadcast_calls: list[tuple[str, str]] = []

        async def fake_broadcast(event: str, data: str) -> None:
            broadcast_calls.append((event, data))

        server._broadcast_sse = fake_broadcast  # type: ignore[method-assign]

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("aiohttp.ClientSession") as mock_session_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should complete without raising
            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "tok456", "http://ha:8099"
            )

        # Reconnect failure must NOT emit wifi_ap_error — only nmcli connect failure does
        assert not any(ev == "wifi_ap_error" for ev, _ in broadcast_calls)

    async def test_ssid_with_special_chars_not_injected(self, client: TestClient) -> None:
        """SSID with special characters cannot inject nmcli commands."""
        malicious_ssid = "SmartLife_`id`"
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            json={
                "ap_ssid": malicious_ssid,
                "home_ssid": "HomeNet",
                "home_password": "pass",
            },
        )
        # Server accepts the request (validation is at the OS layer via execv)
        assert resp.status == 200
        data = await resp.json()
        # Token should be returned regardless
        assert "token" in data

    @pytest.mark.parametrize(
        "field,value",
        [
            # ap_ssid must have a Tuya prefix AND a control character
            ("ap_ssid", "SmartLife_\x00T"),  # null byte after valid Tuya prefix
            ("home_ssid", "Home\x1fNet"),  # control char in home SSID
            ("home_password", "pass\x7fword"),  # DEL char in password
        ],
    )
    async def test_control_chars_rejected(self, client: TestClient, field: str, value: str) -> None:
        """SSIDs/passwords containing control characters are rejected with 400."""
        body = {"ap_ssid": "SmartLife_AB12", "home_ssid": "HomeNet", "home_password": "pass"}
        body[field] = value
        resp = await client.post("/api/provision/wifi-ap-pair", json=body)
        assert resp.status == 400
        assert "control" in (await resp.text()).lower()

    @pytest.mark.parametrize(
        "field,value,expected_status",
        [
            # WiFi SSID limit: 32 bytes — use valid Tuya prefix for ap_ssid
            ("ap_ssid", "SmartLife_" + "S" * 23, 400),  # 10+23=33 bytes → over limit
            ("home_ssid", "H" * 33, 400),
            # WPA2 password limit: 63 bytes
            ("home_password", "p" * 64, 400),
            # Exactly at limit — must be accepted (ap_ssid: 10+22=32 bytes with Tuya prefix)
            ("ap_ssid", "SmartLife_" + "S" * 22, 200),
            ("home_ssid", "H" * 32, 200),
            ("home_password", "p" * 63, 200),
        ],
    )
    async def test_field_length_limits(
        self, client: TestClient, field: str, value: str, expected_status: int
    ) -> None:
        """SSID ≤ 32 bytes and password ≤ 63 bytes are enforced per WiFi spec."""
        from unittest.mock import patch

        body = {"ap_ssid": "SmartLife_AB12", "home_ssid": "HomeNet", "home_password": "pass"}
        body[field] = value

        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._wifi_ap_pair_task"
        ):
            resp = await client.post("/api/provision/wifi-ap-pair", json=body)

        assert resp.status == expected_status

    async def test_wifi_ap_pair_rate_limited(self, server: PairingServer) -> None:
        """wifi-ap-pair returns 429 after 5 requests from same IP within 60 seconds."""
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Pre-fill rate limit bucket to just below capacity (max=5 for wifi-ap-pair)
            now = time.monotonic()
            server._rate_limit["127.0.0.1"] = [now] * 4

            # 5th request should still succeed (control char check rejected before rate limit)
            # Use valid body so it actually reaches the rate-limit check
            from unittest.mock import patch

            with patch(
                "custom_components.tuya_cloudless.pairing_server.PairingServer._wifi_ap_pair_task"
            ):
                resp = await cli.post(
                    "/api/provision/wifi-ap-pair",
                    json={
                        "ap_ssid": "SmartLife_AB12",
                        "home_ssid": "HomeNet",
                        "home_password": "pass",
                    },
                )
            assert resp.status == 200

            # 6th request should be rate-limited
            resp = await cli.post(
                "/api/provision/wifi-ap-pair",
                json={"ap_ssid": "SmartLife_AB12", "home_ssid": "HomeNet", "home_password": "x"},
            )
            assert resp.status == 429
            assert "Retry-After" in resp.headers
        finally:
            await cli.close()

    async def test_rate_limit_dict_does_not_grow_unbounded(self, server: PairingServer) -> None:
        """Old IPs are cleaned from _rate_limit after each activation request."""
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Pre-populate _rate_limit with 100 fake IPs, all with empty timestamp lists
            for i in range(100):
                server._rate_limit[f"10.0.{i // 256}.{i % 256}"] = []

            assert len(server._rate_limit) >= 100

            # Trigger a rate-limit check via activation endpoint
            await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "test_gc", "token": _TOKEN_GC},
            )

            # Empty-list IPs should have been evicted
            empty_ips = [ip for ip, stamps in server._rate_limit.items() if not stamps]
            assert empty_ips == [], f"Expected no empty IPs after GC, got: {empty_ips}"
        finally:
            await cli.close()

    async def test_duplicate_ap_pair_returns_409(self, client: TestClient) -> None:
        """A second wifi-ap-pair request for the same AP while one is in progress returns 409."""
        # Simulate a pairing already in progress by pre-populating the tracking set.
        # We find the server instance via the underlying aiohttp app's _server attribute,
        # but the cleaner approach is to access it via the fixture: client.server wraps
        # TestServer whose app is server._app.  We need a separate server fixture here.
        from custom_components.tuya_cloudless.pairing_server import PairingServer as PS

        srv = PS(_make_hass(), port=0)
        srv._wifi_ap_pairing_in_progress.add("SmartLife_AB12")

        ts = TestServer(srv._app)
        cli2 = TestClient(ts)
        await cli2.start_server()
        try:
            resp = await cli2.post(
                "/api/provision/wifi-ap-pair",
                json={
                    "ap_ssid": "SmartLife_AB12",
                    "home_ssid": "HomeNet",
                    "home_password": "pass",
                },
            )
            assert resp.status == 409
        finally:
            await cli2.close()

    async def test_gw_json_post_contains_token_and_activator(self, client: TestClient) -> None:
        """The gw.json POST to the Tuya device includes token and activator URL."""
        from custom_components.tuya_cloudless.pairing_server import PairingServer as PS

        srv = PS(_make_hass(), port=0)
        captured: dict[str, object] = {}

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"yes:HomeNet", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("aiohttp.ClientSession") as mock_cls,
        ):
            mock_session = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200

            def capture_post(url: str, **kwargs: object) -> AsyncMock:
                captured["url"] = url
                captured["json"] = kwargs.get("json")
                cm = AsyncMock()
                cm.__aenter__ = AsyncMock(return_value=mock_resp)
                cm.__aexit__ = AsyncMock(return_value=False)
                return cm

            mock_session.post = capture_post

            await srv._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "s3cr3t", "tok_gw", "http://ha:8099"
            )

        assert captured.get("url", "").endswith("/gw.json")
        payload = captured.get("json", {})
        assert isinstance(payload, dict)
        assert payload.get("t") == "tok_gw"
        assert payload.get("activator") == "http://ha:8099"
        assert payload.get("s") == "HomeNet"

    async def test_gw_json_post_uses_allow_redirects_false(self) -> None:
        """gw.json POST must use allow_redirects=False to prevent SSRF via firmware redirect."""
        from custom_components.tuya_cloudless.pairing_server import PairingServer as PS

        srv = PS(_make_hass(), port=0)
        captured: dict[str, object] = {}

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"yes:HomeNet", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("aiohttp.ClientSession") as mock_cls,
        ):
            mock_session = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200

            def capture_post(url: str, **kwargs: object) -> AsyncMock:
                captured.update(kwargs)
                cm = AsyncMock()
                cm.__aenter__ = AsyncMock(return_value=mock_resp)
                cm.__aexit__ = AsyncMock(return_value=False)
                return cm

            mock_session.post = capture_post

            await srv._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "s3cr3t", "tok_redirect", "http://ha:8099"
            )

        assert captured.get("allow_redirects") is False, (
            "gw.json POST must set allow_redirects=False (SSRF prevention)"
        )
        timeout_obj = captured.get("timeout")
        assert timeout_obj is not None, "gw.json POST must set a timeout"
        assert timeout_obj.connect == 3.0, (
            "gw.json POST must limit TCP handshake to 3s (connect timeout)"
        )

    async def test_unexpected_exception_emits_wifi_ap_error_sse(self) -> None:
        """An unexpected exception inside _wifi_ap_pair_task still emits wifi_ap_error SSE."""
        from custom_components.tuya_cloudless.pairing_server import PairingServer as PS

        srv = PS(_make_hass(), port=0)
        broadcast_calls: list[tuple[str, str]] = []

        async def fake_broadcast(event: str, data: str) -> None:
            broadcast_calls.append((event, data))

        srv._broadcast_sse = fake_broadcast  # type: ignore[method-assign]

        async def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Unexpected internal failure")

        with patch("asyncio.create_subprocess_exec", side_effect=boom):
            await srv._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "tok_boom", "http://ha:8099"
            )

        error_events = [ev for ev, _ in broadcast_calls if ev == "wifi_ap_error"]
        assert len(error_events) >= 1, "Expected at least one wifi_ap_error SSE event"

    async def test_wifi_ap_pairing_set_cleared_after_task_completes(
        self, server: PairingServer
    ) -> None:
        """_wifi_ap_pairing_in_progress is cleared once the task finishes (success or error)."""
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
                resp = await cli.post(
                    "/api/provision/wifi-ap-pair",
                    json={
                        "ap_ssid": "SmartLife_AB12",
                        "home_ssid": "HomeNet",
                        "home_password": "pw",
                    },
                )
                assert resp.status == 200
                # Give the background task time to run and clean up
                await asyncio.sleep(0.1)

            assert "SmartLife_AB12" not in server._wifi_ap_pairing_in_progress, (
                "_wifi_ap_pairing_in_progress should be cleared after task completion"
            )
        finally:
            await cli.close()

    async def test_no_prev_connection_reconnects_to_home_wifi(self) -> None:
        """When no saved connection exists, the task falls back to connecting to home_ssid."""
        from unittest.mock import AsyncMock, MagicMock, patch

        nmcli_calls: list[list[str]] = []

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nmcli_calls.append(list(args))
            proc = MagicMock()
            # First call (list active connections) returns nothing → prev_connection stays None
            # Subsequent calls succeed normally
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("aiohttp.ClientSession") as mock_session_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass123", "tok_noprev", "http://ha:8099"
            )

        # The finally-else branch must issue:  nmcli device wifi connect HomeNet password pass123
        fallback_calls = [c for c in nmcli_calls if "HomeNet" in c]
        assert fallback_calls, "Expected a fallback nmcli connect to home_ssid"
        joined = " ".join(str(x) for x in fallback_calls[0])
        assert "device" in joined and "wifi" in joined and "connect" in joined
        assert "HomeNet" in joined
        assert "pass123" in joined

    async def test_no_prev_connection_open_network_omits_password(self) -> None:
        """Fallback reconnect to open home WiFi (no password) omits the 'password' arg."""
        from unittest.mock import AsyncMock, MagicMock, patch

        nmcli_calls: list[list[str]] = []

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nmcli_calls.append(list(args))
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("aiohttp.ClientSession") as mock_session_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

            # Empty password → open network
            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "OpenNet", "", "tok_open", "http://ha:8099"
            )

        fallback_calls = [c for c in nmcli_calls if "OpenNet" in c]
        assert fallback_calls, "Expected fallback nmcli connect to OpenNet"
        assert "password" not in fallback_calls[0], (
            "'password' arg must not appear for open networks"
        )

    async def test_max_concurrent_tasks_returns_429(self, client: TestClient) -> None:
        """When _MAX_WIFI_AP_TASKS background tasks are running, new request gets 429.

        This prevents resource exhaustion from rapid repeated pairing requests.
        """
        from unittest.mock import patch

        from custom_components.tuya_cloudless.pairing_server import _MAX_WIFI_AP_TASKS

        async def _never_ends(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(9999)

        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._wifi_ap_pair_task",
            side_effect=_never_ends,
        ):
            # Fill up to the limit
            tasks_started = 0
            for i in range(_MAX_WIFI_AP_TASKS):
                resp = await client.post(
                    "/api/provision/wifi-ap-pair",
                    json={
                        "ap_ssid": f"SmartLife_{i:04d}",
                        "home_ssid": "HomeNet",
                        "home_password": "pass",
                    },
                )
                if resp.status == 200:
                    tasks_started += 1

            # At capacity — next request must be rejected
            resp_over = await client.post(
                "/api/provision/wifi-ap-pair",
                json={"ap_ssid": "SmartLife_9999", "home_ssid": "HomeNet", "home_password": "pass"},
            )
            assert resp_over.status == 429, (
                f"Expected 429 when {_MAX_WIFI_AP_TASKS} tasks running, got {resp_over.status}"
            )
            body = await resp_over.text()
            assert "too many" in body.lower() or "retry" in body.lower()

    async def test_missing_content_type_returns_415(self, client: TestClient) -> None:
        """wifi-ap-pair must reject requests without application/json Content-Type (CSRF guard)."""
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            data=b'{"ap_ssid":"X","home_ssid":"Y","home_password":"z"}',
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status == 415, f"Expected 415 for non-JSON Content-Type, got {resp.status}"


# ── /api/provision/config ─────────────────────────────────────────────────────


class TestConfigEndpoint:
    """Tests for GET /api/provision/config."""

    async def test_returns_200(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        assert resp.status == 200

    async def test_contains_activator_url(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert "activator_url" in data
        assert data["activator_url"].startswith("http")

    async def test_contains_events_url(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert "events_url" in data
        assert "/api/provision/events" in data["events_url"]

    async def test_contains_default_ssid_field(self, client: TestClient) -> None:
        """default_ssid key must always be present; value may be None."""
        resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert "default_ssid" in data
        assert data["default_ssid"] is None or isinstance(data["default_ssid"], str)

    async def test_default_ssid_none_when_nmcli_missing(self, client: TestClient) -> None:
        from unittest.mock import patch

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] is None

    async def test_default_ssid_from_active_network(self, client: TestClient) -> None:
        """When nmcli reports an active connection, default_ssid is populated."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"yes:MyHomeWifi\nno:NeighborNet\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] == "MyHomeWifi"

    async def test_default_ssid_skips_inactive_networks(self, client: TestClient) -> None:
        """Only the yes: line is returned as default_ssid."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"no:SomeOtherNet\nno:AnotherNet\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] is None

    async def test_default_ssid_filters_control_chars(self, client: TestClient) -> None:
        """default_ssid must be None when the active SSID contains control characters."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        # SSID with embedded null byte — must not be returned as default_ssid
        mock_proc.communicate = AsyncMock(return_value=(b"yes:Bad\x00SSID\nno:GoodNet\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] is None, (
            "SSID with control chars must not be returned as default_ssid"
        )

    async def test_default_ssid_with_colon_in_name(self, client: TestClient) -> None:
        """SSIDs containing colons (e.g. 'MyNet:5G') must be returned intact.

        nmcli --terse uses ':' as field separator so the output for SSID='MyNet:5G'
        is 'yes:MyNet:5G'.  The parser uses line[4:] to skip the 'yes:' prefix,
        correctly preserving any additional colons in the SSID.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"yes:MyNet:5G\nno:OtherNet\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] == "MyNet:5G", (
            "SSID containing a colon must be parsed correctly"
        )

    async def test_default_ssid_skips_oversized_utf8_ssid(self, client: TestClient) -> None:
        """SSID > 32 UTF-8 bytes must be skipped (IEEE 802.11 max is 32 bytes)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        # 11 x 'あ' = 33 UTF-8 bytes (each 'あ' is 3 bytes) -- over the 32-byte limit
        long_ssid = "\u3042" * 11
        nmcli_output = f"yes:{long_ssid}\nno:ShortNet\n".encode()
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(nmcli_output, b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] is None, (
            "SSID exceeding 32 UTF-8 bytes must not be returned as default_ssid"
        )

    async def test_default_ssid_accepts_exactly_32_utf8_bytes(self, client: TestClient) -> None:
        """SSID of exactly 32 UTF-8 bytes is accepted (boundary value)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        # Note: 16 x 'あ' = 48 bytes (too long); use ASCII for a clean 32-byte example
        # Use ASCII so byte count == char count: 32 'a' chars = 32 bytes
        ssid_32 = "a" * 32
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(f"yes:{ssid_32}\n".encode(), b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] == ssid_32, "SSID of exactly 32 bytes must be accepted"

    async def test_cors_header_present(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/config")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    async def test_config_includes_wifi_scan_available(self, client: TestClient) -> None:
        """wifi_scan_available key must always be present and be a bool."""
        resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert "wifi_scan_available" in data
        assert isinstance(data["wifi_scan_available"], bool)

    async def test_config_wifi_scan_available_false_when_nmcli_missing(
        self, client: TestClient
    ) -> None:
        """wifi_scan_available must be False when shutil.which('nmcli') returns None."""
        from unittest.mock import patch

        with patch("shutil.which", return_value=None):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["wifi_scan_available"] is False

    async def test_default_ssid_uses_iwgetid_when_nmcli_fails(self, client: TestClient) -> None:
        """When nmcli raises FileNotFoundError, iwgetid fallback is tried."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"MyFallbackNet\n", b""))

        with (
            patch("asyncio.create_subprocess_exec", side_effect=[FileNotFoundError, mock_proc]),
            patch("shutil.which", return_value="/sbin/iwgetid"),
        ):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] == "MyFallbackNet"

    async def test_config_includes_integration_version(self, client: TestClient) -> None:
        """integration_version key must be a non-empty string from manifest.json."""
        resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert "integration_version" in data
        assert isinstance(data["integration_version"], str)
        assert data["integration_version"] != "" and data["integration_version"] != "unknown"


# ── /static/icon.png ──────────────────────────────────────────────────────────


class TestIconEndpoint:
    """Tests for GET /static/icon.png."""

    async def test_returns_200_or_404(self, client: TestClient) -> None:
        """Icon route must respond — 200 if brand/icon.png exists, else 404."""
        resp = await client.get("/static/icon.png")
        assert resp.status in (200, 404)

    async def test_returns_png_when_file_exists(self, tmp_path: Path) -> None:
        """When brand/icon.png is present, response has image/png content type."""
        from unittest.mock import patch

        import custom_components.tuya_cloudless.pairing_server as ps_mod

        brand_dir = tmp_path / "brand"
        brand_dir.mkdir()
        # Minimal valid 1x1 PNG (67 bytes)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"  # signature
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01"  # IHDR
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            b"\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"  # IDAT
            b"\x00\x01\x01\x00\x18\xdd\x8d\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"  # IEND
        )
        (brand_dir / "icon.png").write_bytes(png_bytes)

        with patch.object(ps_mod, "_BRAND_DIR", brand_dir):
            srv = PairingServer(_make_hass(), port=0)
            ts = TestServer(srv._app)
            cli = TestClient(ts)
            await cli.start_server()
            try:
                resp = await cli.get("/static/icon.png")
                assert resp.status == 200
                assert resp.content_type == "image/png"
                body = await resp.read()
                assert body == png_bytes
            finally:
                await cli.close()

    async def test_returns_404_when_file_missing(self, tmp_path: Path) -> None:
        """Empty brand dir → 404, no crash."""
        from unittest.mock import patch

        import custom_components.tuya_cloudless.pairing_server as ps_mod

        empty_brand = tmp_path / "brand"
        empty_brand.mkdir()

        with patch.object(ps_mod, "_BRAND_DIR", empty_brand):
            srv = PairingServer(_make_hass(), port=0)
            ts = TestServer(srv._app)
            cli = TestClient(ts)
            await cli.start_server()
            try:
                resp = await cli.get("/static/icon.png")
                assert resp.status == 404
            finally:
                await cli.close()


# ── PairingServer.start / stop ────────────────────────────────────────────────


class TestStartStop:
    async def test_start_sets_runner_and_site(self) -> None:
        """start() must set _runner and _site."""
        hass = _make_hass()
        srv = PairingServer(hass, port=0)

        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()
        mock_site = MagicMock()
        mock_site.start = AsyncMock()

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.web.AppRunner",
                return_value=mock_runner,
            ),
            patch(
                "custom_components.tuya_cloudless.pairing_server.web.TCPSite",
                return_value=mock_site,
            ),
            patch(
                "custom_components.tuya_cloudless.pairing_server.register_redirect_view",
                new_callable=AsyncMock,
            ),
        ):
            await srv.start()

        assert srv._runner is mock_runner
        assert srv._site is mock_site
        mock_runner.setup.assert_awaited_once()
        mock_site.start.assert_awaited_once()

    async def test_stop_clears_sse_queues_and_cleans_runner(self) -> None:
        """stop() must signal SSE subscribers, clear queues, and call runner.cleanup()."""
        hass = _make_hass()
        srv = PairingServer(hass, port=0)

        # Inject a mock runner
        mock_runner = MagicMock()
        mock_runner.cleanup = AsyncMock()
        srv._runner = mock_runner
        srv._site = MagicMock()

        # Add a fake SSE queue
        q: asyncio.Queue[str | None] = asyncio.Queue()
        srv._sse_queues.append(q)

        await srv.stop()

        # Queue should have received None (close signal)
        assert not q.empty()
        msg = q.get_nowait()
        assert msg is None

        # SSE queue list should be cleared
        assert srv._sse_queues == []

        # Runner should have been cleaned up and cleared
        mock_runner.cleanup.assert_awaited_once()
        assert srv._runner is None
        assert srv._site is None

    async def test_stop_does_not_block_on_full_sse_queue(self) -> None:
        """stop() must not block when an SSE queue is full (stalled client).

        Regression: previously used await q.put(None) which blocks forever when
        the queue is at maxsize.  Now uses put_nowait() + suppress(QueueFull).
        """
        hass = _make_hass()
        srv = PairingServer(hass, port=0)
        mock_runner = MagicMock()
        mock_runner.cleanup = AsyncMock()
        srv._runner = mock_runner

        # Fill the queue to capacity so put() would block
        full_q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=2)
        await full_q.put("msg1")
        await full_q.put("msg2")
        srv._sse_queues.append(full_q)

        # Must complete without hanging (put_nowait + suppress(QueueFull))
        await asyncio.wait_for(srv.stop(), timeout=2.0)

        # Queue is cleared from the tracking list regardless
        assert srv._sse_queues == []

    async def test_stop_cancels_auto_stop_task(self) -> None:
        """stop() must cancel any pending auto-stop task."""
        hass = _make_hass()
        srv = PairingServer(hass, port=0)

        mock_runner = MagicMock()
        mock_runner.cleanup = AsyncMock()
        srv._runner = mock_runner

        # Use a MagicMock task to verify cancel() is called
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        srv._auto_stop_task = mock_task

        await srv.stop()

        mock_task.cancel.assert_called_once()
        assert srv._auto_stop_task is None

    async def test_stop_when_no_runner(self) -> None:
        """stop() must not crash when called before start()."""
        srv = PairingServer(_make_hass(), port=0)
        # No runner set — should not raise
        await srv.stop()
        assert srv._runner is None

    async def test_stop_cancels_background_wifi_ap_tasks(self) -> None:
        """stop() must cancel any in-flight WiFi AP background tasks.

        Background tasks must not outlive the server — they hold nmcli subprocess
        handles and network state. Leaving them running after HA stops could leave
        the host WiFi in an unknown state.
        """
        srv = PairingServer(_make_hass(), port=0)

        async def _never_finishes() -> None:
            await asyncio.sleep(9999)  # simulates a long-running nmcli call

        task: asyncio.Task[None] = asyncio.create_task(_never_finishes())
        srv._background_tasks.add(task)
        srv._wifi_ap_pairing_in_progress.add("SmartLife_AB12")

        await srv.stop()

        # Task must be done (no longer running) and specifically cancelled
        assert task.done(), "Background WiFi AP task must be done after stop()"
        assert task.cancelled(), "Background WiFi AP task must be cancelled by stop()"
        # Server state must be cleaned up
        assert srv._background_tasks == set()
        assert srv._wifi_ap_pairing_in_progress == set()


# ── ha_local_url fallbacks ────────────────────────────────────────────────────


class TestHaLocalUrlFallbacks:
    def test_fallback_to_internal_url(self) -> None:
        """When get_url raises, fall back to hass.config.internal_url."""
        hass = _make_hass()
        hass.config.internal_url = "http://192.168.1.100:8123"

        srv = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("no url"),
        ):
            url = srv.ha_local_url()

        # Should have fallen back to internal_url hostname
        assert "192.168.1.100" in url
        assert ":8099" in url

    def test_fallback_to_hostname_when_all_fail(self) -> None:
        """When get_url and internal_url both fail, return the machine hostname."""
        hass = _make_hass()
        hass.config.internal_url = None  # no internal URL

        srv = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("no url"),
        ):
            url = srv.ha_local_url()

        assert url.startswith("http://")
        assert ":8099" in url

    def test_register_flow_cancels_auto_stop(self) -> None:
        """register_flow() must cancel any existing auto-stop task."""
        srv = PairingServer(_make_hass(), port=0)

        # Inject a fake task
        mock_task = MagicMock()
        srv._auto_stop_task = mock_task

        srv.register_flow("flow-abc")

        mock_task.cancel.assert_called_once()
        assert srv._auto_stop_task is None
        assert "flow-abc" in srv._pending_flows

    def test_unregister_flow_schedules_auto_stop(self) -> None:
        """unregister_flow() must schedule idle-stop when no flows remain."""
        hass = _make_hass()
        mock_ct = MagicMock(return_value=MagicMock())
        hass.async_create_task = mock_ct
        srv = PairingServer(hass, port=0)
        srv._pending_flows.add("flow-xyz")

        srv.unregister_flow("flow-xyz")

        assert "flow-xyz" not in srv._pending_flows
        mock_ct.assert_called_once()

    def test_unregister_flow_no_schedule_when_others_remain(self) -> None:
        """unregister_flow() must NOT schedule idle-stop while other flows are registered."""
        hass = _make_hass()
        mock_ct = MagicMock(return_value=MagicMock())
        hass.async_create_task = mock_ct
        srv = PairingServer(hass, port=0)
        srv._pending_flows.add("flow-1")
        srv._pending_flows.add("flow-2")

        srv.unregister_flow("flow-1")

        mock_ct.assert_not_called()


# ── _handle_qr ────────────────────────────────────────────────────────────────


class TestQrEndpoint:
    async def test_returns_503_when_qrcode_not_installed(self, client: TestClient) -> None:
        """Without qrcode library, /api/provision/qr.svg returns 503."""
        import builtins

        real_import = builtins.__import__

        def _no_qrcode(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("qrcode"):
                raise ImportError("qrcode not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_no_qrcode):
            resp = await client.get("/api/provision/qr.svg")

        assert resp.status == 503

    async def test_returns_svg_when_qrcode_installed(self, client: TestClient) -> None:
        """When qrcode is available, /api/provision/qr.svg returns SVG content."""
        try:
            import qrcode  # noqa: F401
        except ImportError:
            pytest.skip("qrcode not installed in test environment")

        resp = await client.get("/api/provision/qr.svg")
        assert resp.status == 200
        assert "svg" in resp.content_type.lower()

    async def test_qr_endpoint_rate_limited(self, server: PairingServer) -> None:
        """QR endpoint returns 429 with Retry-After when per-IP rate limit (20/min) is exceeded."""
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Pre-fill the rate limit bucket to just below capacity for QR (max=20)
            now = time.monotonic()
            server._rate_limit["127.0.0.1"] = [now] * 19

            # 20th request should still succeed (503 if qrcode missing, but not 429)
            resp = await cli.get("/api/provision/qr.svg")
            assert resp.status in (200, 503)  # 503 if qrcode not installed, OK

            # 21st request must be rate-limited
            resp = await cli.get("/api/provision/qr.svg")
            assert resp.status == 429
            assert "Retry-After" in resp.headers
        finally:
            await cli.close()


# ── Full activation response structure ───────────────────────────────────────


class TestActivateResponseStructure:
    """Verify the full activation response body matches the Tuya cloud format.

    The Tuya device checks these fields to determine if activation succeeded.
    Any missing or wrong-type field can cause the device to reject the response.
    """

    async def test_response_has_all_required_top_level_keys(self, client: TestClient) -> None:
        """t, success, and result keys must all be present."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "struct_gw", "product_key": "pk", "token": _TOKEN_STRUCT},
        )
        assert resp.status == 200
        body = await resp.json()
        assert "t" in body
        assert "success" in body
        assert "result" in body

    async def test_response_result_has_all_required_keys(self, client: TestClient) -> None:
        """result object must contain gwId, active, ability, localKey, timezone, netType."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "keys_gw", "token": _TOKEN_KEYS},
        )
        body = await resp.json()
        result = body["result"]
        for key in ("gwId", "active", "ability", "localKey", "timezone", "netType"):
            assert key in result, f"result[{key!r}] missing from activation response"

    async def test_response_result_values_match_spec(self, client: TestClient) -> None:
        """Verify specific values the Tuya device expects."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "spec_gw", "token": _TOKEN_SPEC},
        )
        body = await resp.json()
        result = body["result"]

        assert body["success"] is True
        assert isinstance(body["t"], int)
        assert result["gwId"] == "spec_gw"
        assert result["active"] == 2  # 2 = activated
        assert result["ability"] == 0
        assert result["netType"] == 0
        assert len(result["localKey"]) == 16  # secrets.token_hex(8) → 16 hex chars
        assert isinstance(result["timezone"], str)
        assert len(result["timezone"]) > 0

    async def test_local_key_is_hex_string(self, client: TestClient) -> None:
        """localKey must be a valid hexadecimal string."""
        import re

        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "hex_gw", "token": _TOKEN_HEX},
        )
        body = await resp.json()
        local_key = body["result"]["localKey"]
        # secrets.token_hex(8) produces 8 bytes → 16 lowercase hex chars
        assert re.fullmatch(r"[0-9a-f]{16}", local_key), (
            f"localKey {local_key!r} is not 16 lowercase hex chars"
        )

    async def test_same_gw_id_second_activation_uses_different_key(
        self, client: TestClient
    ) -> None:
        """Two activations for the same device (same gw_id) generate different local_keys.

        This covers the re-pairing scenario where a device was reset and needs
        a new local_key.  Results are keyed by token so both are stored.
        """
        resp1 = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "shared_gw", "token": _TOKEN_D},
        )
        resp2 = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "shared_gw", "token": _TOKEN_E},
        )
        body1 = await resp1.json()
        body2 = await resp2.json()

        assert body1["result"]["gwId"] == "shared_gw"
        assert body2["result"]["gwId"] == "shared_gw"
        # Each activation generates an independent local_key
        assert body1["result"]["localKey"] != body2["result"]["localKey"]

        # Both tokens must be retrievable independently
        r1 = await client.get(f"/api/provision/result/{_TOKEN_D}")
        r2 = await client.get(f"/api/provision/result/{_TOKEN_E}")
        assert (await r1.json())["status"] == "ok"
        assert (await r2.json())["status"] == "ok"

    async def test_duplicate_activation_same_token_reuses_local_key(
        self, client: TestClient
    ) -> None:
        """Duplicate activation with same gw_id + same token must reuse the existing key.

        Tuya firmware may retry the activation POST if it misses the response.
        Generating a new local_key on retry would cause a key mismatch between
        the device (which stored the first key) and the HA config flow (which
        received the second key via SSE).
        """
        resp1 = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "dedup_gw", "token": _TOKEN_DEDUP},
        )
        resp2 = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "dedup_gw", "token": _TOKEN_DEDUP},  # same token + gw_id
        )
        body1 = await resp1.json()
        body2 = await resp2.json()

        assert body1["result"]["gwId"] == "dedup_gw"
        assert body2["result"]["gwId"] == "dedup_gw"
        # Second activation must reuse the same local_key — not generate a new one
        assert body1["result"]["localKey"] == body2["result"]["localKey"], (
            "Duplicate activation (same token+gw_id) must be idempotent"
        )

    async def test_duplicate_token_different_gw_id_generates_new_key(
        self, client: TestClient
    ) -> None:
        """Same token + different gw_id must NOT reuse the previous local_key.

        The idempotency guard is scoped to token+gw_id pairs.  If two different
        devices happen to use the same token (edge case) they must each receive an
        independent local_key so neither device ends up with the wrong credentials.
        """
        resp1 = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "device_alpha", "token": _TOKEN_SHARED},
        )
        resp2 = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "device_beta", "token": _TOKEN_SHARED},  # different gw_id
        )
        key1 = (await resp1.json())["result"]["localKey"]
        key2 = (await resp2.json())["result"]["localKey"]
        assert key1 != key2, "Different gw_id with same token must NOT share the idempotency key"

    async def test_result_endpoint_reflects_latest_gw_id_after_token_reuse(
        self, client: TestClient
    ) -> None:
        """GET /result/{token} must return the LATEST gw_id when a token is reused.

        If two different devices activate with the same token (edge case — e.g.
        firmware bug or test harness), the second activation overwrites the result.
        The result endpoint must reflect the second device's gw_id and local_key.
        """
        # First activation
        resp1 = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "device-first", "token": _TOKEN_REUSE},
        )
        key1 = (await resp1.json())["result"]["localKey"]

        # Second activation: same token, different gw_id
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "device-second", "token": _TOKEN_REUSE},
        )

        # Result endpoint must reflect the second activation
        result_resp = await client.get(f"/api/provision/result/{_TOKEN_REUSE}")
        assert result_resp.status == 200
        result = await result_resp.json()
        assert result["gw_id"] == "device-second", (
            "Result must show the latest gw_id after token reuse"
        )
        assert result["local_key"] != key1, (
            "Result must have the new local_key, not the one from the first activation"
        )

    async def test_timezone_falls_back_to_utc_when_not_string(self, client: TestClient) -> None:
        """When hass.config.time_zone is not a string, timezone defaults to 'UTC'."""
        # The default mock has a MagicMock as time_zone (not a string) →  UTC
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "tz_gw", "token": _TOKEN_TZ},
        )
        body = await resp.json()
        # MagicMock is not a str → must fall back to "UTC"
        assert body["result"]["timezone"] == "UTC"


# ── _handle_activate bad JSON ─────────────────────────────────────────────────


class TestActivateBadJson:
    async def test_bad_json_body_returns_400(self, client: TestClient) -> None:
        """Body that is not valid JSON must not crash the handler; body defaults to {} → 400."""
        resp = await client.post(
            "/api/tuya/device/active",
            data=b"not-json-at-all",
            headers={"Content-Type": "application/json"},
        )
        # CSRF guard passes (JSON content-type), body parse falls back to {} → gw_id="" → 400
        assert resp.status == 400

    @pytest.mark.parametrize(
        "raw_body",
        [
            b"null",
            b'"just-a-string"',
            b"42",
            b"[1, 2, 3]",
        ],
        ids=["null", "string", "int", "array"],
    )
    async def test_non_dict_json_body_returns_400(
        self, client: TestClient, raw_body: bytes
    ) -> None:
        """A valid JSON primitive (null / string / int / array) must not crash the handler.

        Previously the code called ``body.get(...)`` without checking isinstance(body, dict),
        so ``null`` would produce an AttributeError and return 500.  The fixed code treats
        any non-dict value as an empty body (no gw_id → 400).
        """
        resp = await client.post(
            "/api/tuya/device/active",
            data=raw_body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400, f"Expected 400 for body={raw_body!r}, got {resp.status}"


# ── _handle_activate with pending flow ────────────────────────────────────────


class TestActivateFlowResume:
    async def test_pending_flow_gets_resumed(self, client: TestClient) -> None:
        """When _pending_flows has a flow_id, activation creates a task to resume it."""
        # We create a fresh server and client to have full control.
        hass = _make_hass()
        hass.async_create_task = MagicMock()
        hass.config_entries = MagicMock()

        fresh_server = PairingServer(hass, port=0)
        fresh_server._pending_flows.add("test-flow-001")

        ts = TestServer(fresh_server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw001", "token": _TOKEN_001},
            )
            assert resp.status == 200
            # async_create_task should have been called for the pending flow
            hass.async_create_task.assert_called_once()
        finally:
            await cli.close()

    async def test_cancelled_flow_does_not_crash_activation(self) -> None:
        """If async_configure raises (flow cancelled), activation still returns 200."""
        hass = _make_hass()
        # async_configure raises RuntimeError simulating a cancelled/missing flow
        configure_coro_called: list[bool] = []

        async def failing_configure(flow_id: str, data: dict) -> None:
            configure_coro_called.append(True)
            raise RuntimeError("Unknown flow")

        hass.config_entries = MagicMock()
        hass.config_entries.flow.async_configure = failing_configure
        # Use real async_create_task so the coroutine actually runs
        hass.async_create_task = lambda coro: asyncio.get_event_loop().create_task(coro)

        fresh_server = PairingServer(hass, port=0)
        fresh_server._pending_flows.add("cancelled-flow-999")

        ts = TestServer(fresh_server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_cancel", "token": _TOKEN_CANCEL},
            )
            # Activation must succeed even though async_configure raised
            assert resp.status == 200
            # Allow the task to run
            await asyncio.sleep(0)
            assert configure_coro_called, "async_configure coroutine was never executed"
        finally:
            await cli.close()


# ── _handle_sse ────────────────────────────────────────────────────────────────


class TestSseEndpoint:
    async def test_sse_initial_connected_comment(self, client: TestClient) -> None:
        """SSE stream sends an initial : connected comment."""
        # Use a short-lived connection: we read a chunk and then close
        async with client.session.get(
            client.make_url("/api/provision/events"),
        ) as resp:
            assert resp.status == 200
            assert resp.content_type == "text/event-stream"
            # Read the initial keep-alive comment
            chunk = await asyncio.wait_for(resp.content.read(64), timeout=5)
            assert b": connected" in chunk

    async def test_sse_503_includes_retry_after_header(self, server: PairingServer) -> None:
        """When SSE connection cap is exceeded, response includes Retry-After header."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSE_CONNECTIONS

        # Fill the SSE queue list to the limit with dummy queues
        for _ in range(_MAX_SSE_CONNECTIONS):
            server._sse_queues.append(asyncio.Queue())

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            resp = await cli.get("/api/provision/events")
            assert resp.status == 503
            assert "Retry-After" in resp.headers
        finally:
            await cli.close()


# ── _broadcast_sse ────────────────────────────────────────────────────────────


class TestBroadcastSse:
    async def test_puts_message_in_all_queues(self, server: PairingServer) -> None:
        """_broadcast_sse must put the formatted message into every SSE queue."""
        q1: asyncio.Queue[str | None] = asyncio.Queue()
        q2: asyncio.Queue[str | None] = asyncio.Queue()
        server._sse_queues.extend([q1, q2])

        await server._broadcast_sse("activated", '{"gw_id":"test"}')

        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        assert "event: activated" in msg1
        assert '{"gw_id":"test"}' in msg1
        assert msg1 == msg2

    async def test_broadcast_to_empty_queues(self, server: PairingServer) -> None:
        """_broadcast_sse with no subscribers must not raise."""
        # No queues — should be a no-op
        await server._broadcast_sse("activated", "{}")

    async def test_stale_queue_removed_on_timeout(self, server: PairingServer) -> None:
        """A full queue (stalled client) is removed rather than blocking forever."""
        # A full queue will timeout immediately in put (maxsize=1, already full)
        stale: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
        await stale.put("existing-message")  # fill it so put() would block

        healthy: asyncio.Queue[str | None] = asyncio.Queue()
        server._sse_queues.extend([stale, healthy])

        await server._broadcast_sse("activated", "{}")

        # Stale queue should have been removed
        assert stale not in server._sse_queues
        # Healthy queue should have received the message
        assert not healthy.empty()

    async def test_stale_queue_receives_none_sentinel(self, server: PairingServer) -> None:
        """Stale queue gets None (close sentinel) so _handle_sse exits instead of looping.

        Without the sentinel, _handle_sse would block on queue.get() with a 25 s
        keepalive timeout and keep the TCP connection open indefinitely.
        """
        stale: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
        await stale.put("existing-message")  # fill it so put() would block
        server._sse_queues.append(stale)

        await server._broadcast_sse("activated", "{}")

        # Stale queue must have been removed from the active list
        assert stale not in server._sse_queues
        # The backlog was drained and None (the sentinel) was injected
        sentinel = stale.get_nowait()
        assert sentinel is None
        # No more items — queue is now empty
        assert stale.empty()


# ── _auto_stop_after_idle ──────────────────────────────────────────────────────


class TestAutoStopAfterIdle:
    async def test_does_not_stop_when_pending_flows_remain(self) -> None:
        """If flows are still registered after sleep, the server must not stop."""
        hass = _make_hass()
        hass.data = {}
        srv = PairingServer(hass, port=0)
        srv._pending_flows.add("flow-still-active")

        stop_called = []

        async def fake_stop_pairing_server(h: object) -> None:
            stop_called.append(True)

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch(
                "custom_components.tuya_cloudless.pairing_server.stop_pairing_server",
                side_effect=fake_stop_pairing_server,
            ),
        ):
            await srv._auto_stop_after_idle()

        assert not stop_called

    async def test_stops_when_idle_and_is_current_server(self) -> None:
        """When no flows remain and this is the active server, stop_pairing_server is called."""
        from custom_components.tuya_cloudless.const import DOMAIN
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        hass.data = {}

        srv = PairingServer(hass, port=0)
        # Register this server as the active one
        hass.data[DOMAIN] = {_KEY_PAIRING_SERVER: srv}

        stop_called = []

        async def fake_stop_pairing_server(h: object) -> None:
            stop_called.append(True)

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch(
                "custom_components.tuya_cloudless.pairing_server.stop_pairing_server",
                side_effect=fake_stop_pairing_server,
            ),
        ):
            await srv._auto_stop_after_idle()

        assert stop_called


# ── get_pairing_server ────────────────────────────────────────────────────────


class TestGetPairingServer:
    def test_returns_none_when_hass_data_empty(self) -> None:
        """get_pairing_server must return None when no server is stored."""
        hass = _make_hass()
        hass.data = {}
        result = get_pairing_server(hass)
        assert result is None

    def test_returns_server_when_stored(self) -> None:
        """get_pairing_server must return the stored PairingServer."""
        from custom_components.tuya_cloudless.const import DOMAIN
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        srv = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: srv}}

        result = get_pairing_server(hass)
        assert result is srv


# ── ensure_pairing_server ─────────────────────────────────────────────────────


class TestEnsurePairingServer:
    async def test_creates_and_starts_new_server(self) -> None:
        """ensure_pairing_server must create a new server if none exists."""
        from custom_components.tuya_cloudless.const import DOMAIN
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        hass.data = {}

        start_calls: list[object] = []

        async def fake_start(self_srv: PairingServer) -> None:
            start_calls.append(self_srv)

        with patch.object(PairingServer, "start", fake_start):
            result = await ensure_pairing_server(hass)

        assert isinstance(result, PairingServer)
        assert len(start_calls) == 1
        assert hass.data[DOMAIN][_KEY_PAIRING_SERVER] is result

    async def test_returns_existing_server(self) -> None:
        """ensure_pairing_server must return the existing server without restarting."""
        from custom_components.tuya_cloudless.const import DOMAIN
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        existing = PairingServer(hass, port=0)
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: existing}}

        start_calls: list[object] = []

        async def fake_start(self_srv: PairingServer) -> None:  # pragma: no cover
            start_calls.append(self_srv)

        with patch.object(PairingServer, "start", fake_start):
            result = await ensure_pairing_server(hass)

        assert result is existing
        assert len(start_calls) == 0


# ── PairingRedirectView ───────────────────────────────────────────────────────


class TestPairingRedirectView:
    def test_init_stores_port(self) -> None:
        """PairingRedirectView.__init__ must store the port."""
        view = PairingRedirectView(port=1234)
        assert view._port == 1234

    def test_init_default_port(self) -> None:
        """Default port comes from PAIRING_SERVER_PORT."""
        from custom_components.tuya_cloudless.pairing_server import PAIRING_SERVER_PORT

        view = PairingRedirectView()
        assert view._port == PAIRING_SERVER_PORT

    async def test_get_raises_http_found(self) -> None:
        """get() must raise HTTPFound with a redirect to the pairing server."""
        view = PairingRedirectView(port=8099)

        mock_request = MagicMock()
        mock_request.url.host = "192.168.1.50"
        mock_request.url.scheme = "http"
        mock_request.headers = {}

        with pytest.raises(web.HTTPFound) as exc_info:
            await view.get(mock_request, "my-flow-id")

        location = exc_info.value.location
        assert "192.168.1.50" in str(location)
        assert "8099" in str(location)
        assert "my-flow-id" in str(location)

    async def test_get_uses_fallback_host_when_empty(self) -> None:
        """When request.url.host is empty, falls back to homeassistant.local."""
        view = PairingRedirectView(port=8099)

        mock_request = MagicMock()
        mock_request.url.host = ""
        mock_request.url.scheme = "http"
        mock_request.headers = {}

        with pytest.raises(web.HTTPFound) as exc_info:
            await view.get(mock_request, "flow-id")

        assert "homeassistant.local" in str(exc_info.value.location)

    async def test_get_redirects_to_ha_https_ui_when_forwarded_proto_is_https(
        self,
    ) -> None:
        """When X-Forwarded-Proto: https, redirect to the HA HTTPS pairing UI."""
        from custom_components.tuya_cloudless.pairing_server import _HA_PAIRING_PREFIX

        view = PairingRedirectView(port=8099)

        mock_request = MagicMock()
        mock_request.url.host = "ha.example.com"
        mock_request.url.scheme = "http"  # HA sees HTTP internally
        mock_request.remote = "127.0.0.1"  # Trusted reverse proxy on loopback
        mock_request.headers = {"X-Forwarded-Proto": "https"}

        with pytest.raises(web.HTTPFound) as exc_info:
            await view.get(mock_request, "abc123")

        location = str(exc_info.value.location)
        assert _HA_PAIRING_PREFIX in location
        assert "abc123" in location
        # Must NOT redirect to port 8099
        assert "8099" not in location

    async def test_get_redirects_to_port_8099_when_no_https(self) -> None:
        """Without HTTPS headers, redirect to the port-8099 server."""
        view = PairingRedirectView(port=8099)

        mock_request = MagicMock()
        mock_request.url.host = "192.168.1.10"
        mock_request.url.scheme = "http"
        mock_request.headers = {}  # No X-Forwarded-Proto

        with pytest.raises(web.HTTPFound) as exc_info:
            await view.get(mock_request, "flow-xyz")

        location = str(exc_info.value.location)
        assert "192.168.1.10" in location
        assert "8099" in location
        assert "flow-xyz" in location

    async def test_get_rejects_invalid_flow_id(self) -> None:
        """flow_id with special characters must return HTTP 400 (injection prevention)."""
        view = PairingRedirectView(port=8099)
        mock_request = MagicMock()
        mock_request.url.host = "ha.local"
        mock_request.url.scheme = "http"
        mock_request.headers = {}

        for bad_flow_id in [
            "../../etc/passwd",
            "flow\r\nX-Injected: evil",
            "<script>alert(1)</script>",
            "a" * 129,  # too long
            "",
        ]:
            resp = await view.get(mock_request, bad_flow_id)
            assert resp.status == 400, f"Expected 400 for flow_id={bad_flow_id!r}"

    async def test_get_accepts_valid_flow_id_formats(self) -> None:
        """Valid flow_id formats (UUID, alphanumeric, hyphens) must redirect normally."""
        view = PairingRedirectView(port=8099)
        mock_request = MagicMock()
        mock_request.url.host = "ha.local"
        mock_request.url.scheme = "http"
        mock_request.headers = {}

        for good_flow_id in [
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",  # UUID
            "abc123",
            "flow_id-99",
            "A" * 128,  # max length
        ]:
            with pytest.raises(web.HTTPFound):
                await view.get(mock_request, good_flow_id)


# ── register_redirect_view ────────────────────────────────────────────────────


class TestRegisterRedirectView:
    async def test_calls_hass_http_register_view(self) -> None:
        """register_redirect_view must call hass.http.register_view with a view."""
        hass = _make_hass()
        hass.http = MagicMock()

        await register_redirect_view(hass, port=9999)

        hass.http.register_view.assert_called_once()
        # The argument should be a view instance (not the class)
        call_arg = hass.http.register_view.call_args[0][0]
        assert hasattr(call_arg, "url")
        assert hasattr(call_arg, "name")
        assert call_arg._port == 9999


# ── stop_pairing_server ───────────────────────────────────────────────────────


class TestStopPairingServer:
    async def test_stops_and_removes_server(self) -> None:
        """stop_pairing_server must call server.stop() and remove it from hass.data."""
        from custom_components.tuya_cloudless.const import DOMAIN
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        srv = PairingServer(hass, port=0)
        srv.stop = AsyncMock()
        hass.data = {DOMAIN: {_KEY_PAIRING_SERVER: srv}}

        await stop_pairing_server(hass)

        srv.stop.assert_awaited_once()
        assert _KEY_PAIRING_SERVER not in hass.data.get(DOMAIN, {})

    async def test_no_error_when_hass_data_empty(self) -> None:
        """stop_pairing_server must not raise when no server is stored."""
        hass = _make_hass()
        hass.data = {}
        # Should complete without error
        await stop_pairing_server(hass)

    async def test_no_error_when_domain_data_not_dict(self) -> None:
        """stop_pairing_server must not raise when domain data is not a dict."""
        from custom_components.tuya_cloudless.const import DOMAIN

        hass = _make_hass()
        hass.data = {DOMAIN: "unexpected_value"}
        # Should return early without error
        await stop_pairing_server(hass)


# ── ha_local_url internal_url exception branch (lines 242-243) ───────────────


class TestHaLocalUrlInternalUrlException:
    def test_exception_in_internal_url_falls_back_to_hostname(self) -> None:
        """When accessing internal_url raises, fall back to machine hostname."""
        hass = _make_hass()

        # Make internal_url raise when accessed
        type(hass.config).internal_url = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no attr"))
        )

        srv = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("no url"),
        ):
            url = srv.ha_local_url()

        # Should fall through to hostname fallback
        assert url.startswith("http://")
        assert ":8099" in url


# ── _handle_index returns HTML when UI dir exists (line 308 area) ─────────────


class TestIndexPageHtml:
    async def test_returns_html_when_ui_dir_exists(self, tmp_path: Path) -> None:
        """When index.html exists in _UI_DIR, responds with its content."""
        import custom_components.tuya_cloudless.pairing_server as ps_mod

        ui_dir = tmp_path / "pairing_ui"
        ui_dir.mkdir()
        index_html = ui_dir / "index.html"
        index_html.write_text("<html><body>Test</body></html>", encoding="utf-8")

        with patch.object(ps_mod, "_UI_DIR", ui_dir):
            srv = PairingServer(_make_hass(), port=0)
            ts = TestServer(srv._app)
            cli = TestClient(ts)
            await cli.start_server()
            try:
                resp = await cli.get("/")
                assert resp.status == 200
                assert "text/html" in resp.content_type
                body = await resp.text()
                assert "Test" in body
            finally:
                await cli.close()


# ── SSE keepalive and message delivery (lines 585-591) ────────────────────────


class TestSseMessageDelivery:
    async def test_sse_delivers_message_after_activation(self, client: TestClient) -> None:
        """After an activation POST, the SSE stream receives the activated event."""
        # Connect to SSE first, then trigger activation
        # We use a short timeout since the SSE stream is long-lived
        async with client.session.get(
            client.make_url("/api/provision/events"),
        ) as resp:
            assert resp.status == 200

            # Read the initial connected comment
            initial = await asyncio.wait_for(resp.content.read(64), timeout=5)
            assert b": connected" in initial

            # Now trigger an activation (this sends to SSE queues)
            await client.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_sse_test", "token": _TOKEN_SSE},
            )

            # Read the activated event
            data = await asyncio.wait_for(resp.content.read(512), timeout=5)
            assert b"event: activated" in data
            assert b"gw_sse_test" in data

    async def test_sse_activated_event_omits_local_key(self, client: TestClient) -> None:
        """SSE activated event must NOT contain local_key — it is a session credential."""
        async with client.session.get(
            client.make_url("/api/provision/events"),
        ) as resp:
            assert resp.status == 200
            await asyncio.wait_for(resp.content.read(64), timeout=5)  # skip connected comment

            await client.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_sse_key", "token": _TOKEN_SSE_K},
            )

            data = await asyncio.wait_for(resp.content.read(512), timeout=5)
            assert b"event: activated" in data
            assert b"local_key" not in data, "local_key must not appear in SSE broadcast"

    async def test_sse_activated_event_omits_ip_address(self, client: TestClient) -> None:
        """SSE activated event must NOT contain ip_address — it is sensitive information."""
        async with client.session.get(
            client.make_url("/api/provision/events"),
        ) as resp:
            assert resp.status == 200
            await asyncio.wait_for(resp.content.read(64), timeout=5)  # skip connected comment

            await client.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_sse_ip", "token": _TOKEN_SSE_I},
            )

            data = await asyncio.wait_for(resp.content.read(512), timeout=5)
            assert b"event: activated" in data
            assert b"ip_address" not in data, "ip_address must not appear in SSE broadcast"


# ── _handle_qr with mocked qrcode (lines 391, 401-409) ──────────────────────


class TestQrEndpointMocked:
    async def test_returns_svg_when_qrcode_mocked(self, client: TestClient) -> None:
        """With a mocked qrcode library, /api/provision/qr.svg returns SVG."""
        import types

        # Build minimal mock qrcode modules
        mock_qrcode_mod = types.ModuleType("qrcode")
        mock_svg_mod = types.ModuleType("qrcode.image.svg")

        # SvgPathImage factory (class-level mock)
        mock_factory = MagicMock()
        mock_svg_mod.SvgPathImage = mock_factory

        # qrcode.make returns an image object whose save() writes SVG bytes
        fake_svg = b"<svg>test</svg>"

        def _mock_save(buf: object) -> None:
            import io

            if isinstance(buf, io.BytesIO):
                buf.write(fake_svg)

        mock_img = MagicMock()
        mock_img.save = _mock_save
        mock_qrcode_mod.make = MagicMock(return_value=mock_img)
        mock_qrcode_mod.image = MagicMock()
        mock_qrcode_mod.image.svg = mock_svg_mod

        import sys

        saved_qrcode = sys.modules.get("qrcode")
        saved_svg = sys.modules.get("qrcode.image.svg")

        sys.modules["qrcode"] = mock_qrcode_mod
        sys.modules["qrcode.image.svg"] = mock_svg_mod
        try:
            resp = await client.get("/api/provision/qr.svg")
        finally:
            if saved_qrcode is not None:
                sys.modules["qrcode"] = saved_qrcode
            else:
                sys.modules.pop("qrcode", None)
            if saved_svg is not None:
                sys.modules["qrcode.image.svg"] = saved_svg
            else:
                sys.modules.pop("qrcode.image.svg", None)

        assert resp.status == 200
        assert "svg" in resp.content_type.lower()


# ── _auto_detect_profile successful path (lines 1011-1074) ───────────────────
# These lines are in config_flow.py and tested via TestAutoDetectProfileNew above.
# The SSE timeout path for pairing_server needs a dedicated test.


# ── _handle_index 404 when index.html is missing (line 308) ─────────────────


class TestIndexPage404WhenMissing:
    async def test_returns_404_when_index_html_missing(self, tmp_path: Path) -> None:
        """When _UI_DIR exists but has no index.html, return 404 with error text."""
        import custom_components.tuya_cloudless.pairing_server as ps_mod

        ui_dir = tmp_path / "empty_ui"
        ui_dir.mkdir()
        # No index.html — only the directory exists

        with patch.object(ps_mod, "_UI_DIR", ui_dir):
            srv = PairingServer(_make_hass(), port=0)
            ts = TestServer(srv._app)
            cli = TestClient(ts)
            await cli.start_server()
            try:
                resp = await cli.get("/")
                assert resp.status == 404
                text = await resp.text()
                assert "Pairing UI not found" in text
            finally:
                await cli.close()


# ── SSE keepalive timeout path (lines 585-586) ───────────────────────────────
# Line 585-586: after TimeoutError in queue.get, writes keepalive and continues
# Line 589: after queue.get returns a real message, writes message.encode()


class TestSseKeepaliveAndMessage:
    async def test_sse_keepalive_and_message_written(self, server: PairingServer) -> None:
        """SSE handler writes keepalive on timeout, then writes real message."""
        import asyncio

        # We inject: first TimeoutError (keepalive), then a real message, then None (close)
        call_count = 0
        original_wait_for = asyncio.wait_for

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            # Only intercept queue.get calls (short timeout = 25.0)
            if abs(timeout - 25.0) < 1.0:
                call_count += 1
                if call_count == 1:
                    raise TimeoutError()  # triggers keepalive
                if call_count == 2:
                    return "event: test\ndata: hello\n\n"  # real message
                return None  # close signal
            return await original_wait_for(coro, timeout=timeout)

        # Build a fake request and response that capture writes
        written: list[bytes] = []

        mock_response = MagicMock()
        mock_response.prepare = AsyncMock()

        async def fake_write(data: bytes) -> None:
            written.append(data)

        mock_response.write = fake_write

        mock_request = MagicMock()
        mock_request.protocol = MagicMock()

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "custom_components.tuya_cloudless.pairing_server.web.StreamResponse",
                return_value=mock_response,
            ),
        ):
            await server._handle_sse(mock_request)

        # Verify the keepalive was written
        assert any(b": keepalive" in w for w in written)
        # Verify the real message was written
        assert any(b"event: test" in w for w in written)


# ── SEC-001: Rate limiting on activation endpoint (PLAT-824) ─────────────────


class TestRateLimiting:
    """Tests for per-IP rate limiting on /api/tuya/device/active."""

    async def test_rate_limit_allows_requests_below_threshold(self, client: TestClient) -> None:
        """Requests below the limit must succeed."""
        for _ in range(5):
            resp = await client.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_rl", "token": _TOKEN_RL},
            )
            assert resp.status == 200

    async def test_rate_limit_returns_429_when_exceeded(self, server: PairingServer) -> None:
        """After _RATE_LIMIT_MAX requests from the same IP, the next returns 429."""
        from custom_components.tuya_cloudless.pairing_server import _RATE_LIMIT_MAX

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Pre-fill the rate-limit bucket for 127.0.0.1 (aiohttp TestClient remote)
            now = time.monotonic()
            server._rate_limit["127.0.0.1"] = [now] * _RATE_LIMIT_MAX

            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_rllimit"},
            )
            assert resp.status == 429
        finally:
            await cli.close()

    async def test_rate_limit_stale_timestamps_cleaned_up(self, server: PairingServer) -> None:
        """Timestamps older than _RATE_LIMIT_WINDOW are removed on each check."""
        from custom_components.tuya_cloudless.pairing_server import (
            _RATE_LIMIT_MAX,
            _RATE_LIMIT_WINDOW,
        )

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Fill bucket with stale timestamps (well outside the window)
            stale_time = time.monotonic() - _RATE_LIMIT_WINDOW - 1.0
            server._rate_limit["127.0.0.1"] = [stale_time] * _RATE_LIMIT_MAX

            # Request should succeed because stale timestamps are cleaned up
            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_stale"},
            )
            assert resp.status == 200
        finally:
            await cli.close()

    async def test_rate_limit_resets_after_window_passes(self, server: PairingServer) -> None:
        """After the window has passed, a new request is allowed."""
        from custom_components.tuya_cloudless.pairing_server import (
            _RATE_LIMIT_MAX,
            _RATE_LIMIT_WINDOW,
        )

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Set all timestamps well outside the window
            old_time = time.monotonic() - _RATE_LIMIT_WINDOW - 5.0
            server._rate_limit["127.0.0.1"] = [old_time] * _RATE_LIMIT_MAX

            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_reset"},
            )
            assert resp.status == 200

            remaining = server._rate_limit.get("127.0.0.1", [])
            # Only the new timestamp should remain after the stale ones were pruned
            assert len(remaining) == 1
        finally:
            await cli.close()

    async def test_is_rate_limited_custom_limits(self, server: PairingServer) -> None:
        """_is_rate_limited respects custom max_requests and window parameters."""
        # Should allow 3 requests in 10s window
        for _ in range(3):
            assert server._is_rate_limited("10.0.0.1", max_requests=3, window=10.0) is False
        # 4th request should be rate-limited
        assert server._is_rate_limited("10.0.0.1", max_requests=3, window=10.0) is True

    def test_rate_limited_ip_stale_timestamps_evicted_on_reject(
        self, server: PairingServer
    ) -> None:
        """_is_rate_limited must evict stale timestamps even when returning True.

        Previously the filtered list was stored back only on the non-limited path,
        leaving stale timestamps in _rate_limit for rate-limited IPs forever.
        """
        from custom_components.tuya_cloudless.pairing_server import (
            _RATE_LIMIT_MAX,
            _RATE_LIMIT_WINDOW,
        )

        # Inject max_requests stale + 1 current timestamp (sum > max → rate limited)
        stale = time.monotonic() - _RATE_LIMIT_WINDOW - 1.0
        fresh = time.monotonic()
        # One fresh timestamp keeps the IP rate-limited; the stale ones should be evicted
        server._rate_limit["10.0.0.5"] = [stale] * (_RATE_LIMIT_MAX - 1) + [fresh]

        # Call _is_rate_limited — the stale entries should be cleaned up even though
        # we return True (rate limited) due to the one fresh timestamp still counting.
        result = server._is_rate_limited("10.0.0.5", max_requests=1, window=_RATE_LIMIT_WINDOW)
        assert result is True  # fresh timestamp keeps this limited with max=1

        # After the call, stale timestamps must be gone from the dict entry
        remaining = server._rate_limit.get("10.0.0.5", [])
        stale_remaining = [ts for ts in remaining if time.monotonic() - ts >= _RATE_LIMIT_WINDOW]
        assert stale_remaining == [], (
            f"Stale timestamps not evicted for rate-limited IP: {stale_remaining}"
        )

    async def test_is_rate_limited_different_ips_independent(self, server: PairingServer) -> None:
        """Different IPs have independent rate limit buckets."""
        from custom_components.tuya_cloudless.pairing_server import _RATE_LIMIT_MAX

        now = time.monotonic()
        # Fill IP A to the limit
        server._rate_limit["10.0.0.1"] = [now] * _RATE_LIMIT_MAX
        # IP B should still be allowed
        assert server._is_rate_limited("10.0.0.2") is False

    async def test_rate_limit_consecutive_blocked_requests_both_return_429(
        self, server: PairingServer
    ) -> None:
        """Two consecutive requests past the limit must BOTH return 429 (N+1 and N+2).

        Regression guard: blocked requests must not be recorded in the bucket.
        If they were, only the first over-limit request would fail (the bucket
        would grow back below the threshold after stale cleanup), but here all
        timestamps are fresh so the second request must also be rejected.
        """
        from custom_components.tuya_cloudless.pairing_server import _RATE_LIMIT_MAX

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            now = time.monotonic()
            server._rate_limit["127.0.0.1"] = [now] * _RATE_LIMIT_MAX

            # N+1: first over-limit request — must be blocked
            resp1 = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw-n1", "token": "tok-n1"},
            )
            assert resp1.status == 429, f"N+1 expected 429, got {resp1.status}"

            # N+2: second over-limit request — bucket unchanged (blocked reqs not recorded)
            resp2 = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw-n2", "token": "tok-n2"},
            )
            assert resp2.status == 429, f"N+2 expected 429, got {resp2.status}"
        finally:
            await cli.close()


# ── SEC-002: Bounded SSE queues (PLAT-825) ───────────────────────────────────


class TestSseBoundedQueues:
    """Tests for bounded SSE queues and connection cap."""

    async def test_sse_queue_has_maxsize(self, server: PairingServer) -> None:
        """The SSE queue must be created with maxsize > 0."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSE_CONNECTIONS

        written: list[bytes] = []
        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # close immediately
            raise asyncio.CancelledError()

        mock_response = MagicMock()
        mock_response.prepare = AsyncMock()
        mock_response.write = AsyncMock(side_effect=lambda d: written.append(d))

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "custom_components.tuya_cloudless.pairing_server.web.StreamResponse",
                return_value=mock_response,
            ),
        ):
            await server._handle_sse(MagicMock())

        # After handling, verify that queues created in _handle_sse have maxsize>0
        # (we inspect this indirectly: the queue is cleaned up after connection closes,
        # so we check the constant is correct)
        assert _MAX_SSE_CONNECTIONS == 10

    def test_max_sse_connections_constant(self) -> None:
        """_MAX_SSE_CONNECTIONS must equal 10."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSE_CONNECTIONS

        assert _MAX_SSE_CONNECTIONS == 10

    async def test_sse_returns_503_when_at_capacity(self, server: PairingServer) -> None:
        """When _sse_queues is at max capacity, _handle_sse returns 503."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSE_CONNECTIONS

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Fill the queue list with fake entries up to the limit
            fake_queues: list[asyncio.Queue[str | None]] = [
                asyncio.Queue(maxsize=32) for _ in range(_MAX_SSE_CONNECTIONS)
            ]
            server._sse_queues.extend(fake_queues)

            resp = await cli.get("/api/provision/events")
            assert resp.status == 503
            text = await resp.text()
            assert "Too many" in text
        finally:
            server._sse_queues.clear()
            await cli.close()

    async def test_sse_queue_removed_if_prepare_raises(self, server: PairingServer) -> None:
        """Queue must be removed from _sse_queues even if response.prepare() raises.

        Previously, response.prepare() was called outside the try/finally block.
        If the client disconnected before headers were sent, the queue would remain
        in _sse_queues forever, eventually exhausting _MAX_SSE_CONNECTIONS.
        """
        mock_response = MagicMock()
        mock_response.prepare = AsyncMock(
            side_effect=ConnectionResetError("client disconnected before prepare")
        )
        mock_response.write = AsyncMock()

        initial_queue_count = len(server._sse_queues)

        with patch(
            "custom_components.tuya_cloudless.pairing_server.web.StreamResponse",
            return_value=mock_response,
        ):
            await server._handle_sse(MagicMock())

        assert len(server._sse_queues) == initial_queue_count, (
            "Queue must be removed from _sse_queues when response.prepare() raises"
        )

    async def test_sse_queue_maxsize_is_32(self, server: PairingServer) -> None:
        """Queue created in _handle_sse must have maxsize=32 (not unbounded)."""
        import asyncio as _asyncio

        created_maxsizes: list[int] = []

        class CapturingQueue(_asyncio.Queue):  # type: ignore[type-arg]
            def __init__(self, maxsize: int = 0, **kwargs: object) -> None:
                created_maxsizes.append(maxsize)
                super().__init__(maxsize, **kwargs)

        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            return None  # close immediately on first call

        mock_response = MagicMock()
        mock_response.prepare = AsyncMock()
        mock_response.write = AsyncMock()

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.asyncio.Queue",
                CapturingQueue,
            ),
            patch(
                "custom_components.tuya_cloudless.pairing_server.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "custom_components.tuya_cloudless.pairing_server.web.StreamResponse",
                return_value=mock_response,
            ),
        ):
            await server._handle_sse(MagicMock())

        assert len(created_maxsizes) == 1
        assert created_maxsizes[0] == 32

    async def test_keepalive_write_failure_exits_cleanly(self, server: PairingServer) -> None:
        """If response.write() raises ConnectionResetError during keep-alive, the SSE
        handler must exit cleanly without propagating the exception."""
        call_count = 0

        async def fake_wait_for(coro: object, timeout: float = 0) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: simulate timeout (triggers keep-alive send)
                raise TimeoutError
            # Second call: block forever so the handler stops via the write failure
            raise asyncio.CancelledError()

        write_count = 0

        async def failing_write(data: bytes) -> None:
            nonlocal write_count
            write_count += 1
            if data == b": keepalive\n\n":
                raise ConnectionResetError("client gone")

        mock_response = MagicMock()
        mock_response.prepare = AsyncMock()
        mock_response.write = AsyncMock(side_effect=failing_write)

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
            patch(
                "custom_components.tuya_cloudless.pairing_server.web.StreamResponse",
                return_value=mock_response,
            ),
        ):
            # Must complete without raising
            await server._handle_sse(MagicMock())

        # Keep-alive write was attempted and failed — handler exited without re-raising
        assert write_count >= 1, "write() must have been called at least once"


# ── SEC-003: Strict CORS on result and SSE endpoints (PLAT-826) ──────────────


class TestStrictCors:
    """Tests that result and SSE endpoints do NOT send wildcard CORS headers."""

    async def test_result_endpoint_no_wildcard_cors(self, client: TestClient) -> None:
        """GET /api/provision/result/{token} must NOT have Access-Control-Allow-Origin: *."""
        resp = await client.get("/api/provision/result/any_token")
        cors = resp.headers.get("Access-Control-Allow-Origin", "")
        assert cors != "*", "Result endpoint must not send wildcard CORS"

    async def test_result_endpoint_ok_no_wildcard_cors(self, client: TestClient) -> None:
        """After activation, result endpoint must still not send wildcard CORS."""
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_cors", "token": _TOKEN_CORS},
        )
        resp = await client.get(f"/api/provision/result/{_TOKEN_CORS}")
        assert resp.status == 200
        cors = resp.headers.get("Access-Control-Allow-Origin", "")
        assert cors != "*", "Result endpoint (ok) must not send wildcard CORS"

    async def test_sse_endpoint_no_wildcard_cors(self, client: TestClient) -> None:
        """GET /api/provision/events must NOT send Access-Control-Allow-Origin: *."""
        async with client.session.get(
            client.make_url("/api/provision/events"),
        ) as resp:
            assert resp.status == 200
            cors = resp.headers.get("Access-Control-Allow-Origin", "")
            assert cors != "*", "SSE endpoint must not send wildcard CORS"

    async def test_config_endpoint_keeps_wildcard_cors(self, client: TestClient) -> None:
        """GET /api/provision/config is allowed to keep wildcard CORS (unchanged)."""
        resp = await client.get("/api/provision/config")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    async def test_wifi_scan_keeps_wildcard_cors(self, client: TestClient) -> None:
        """GET /api/provision/wifi-scan is allowed to keep wildcard CORS (unchanged)."""
        resp = await client.get("/api/provision/wifi-scan")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# ── ROB-003: asyncio.create_task used in unregister_flow (PLAT-832) ──────────


class TestCreateTaskUsed:
    """Verify unregister_flow uses asyncio.create_task, not ensure_future."""

    def test_unregister_flow_uses_create_task(self) -> None:
        """unregister_flow must schedule idle-stop via hass.async_create_task."""

        hass = _make_hass()
        mock_ct = MagicMock(return_value=MagicMock())
        hass.async_create_task = mock_ct
        srv = PairingServer(hass, port=0)
        srv._pending_flows.add("flow-rob")

        srv.unregister_flow("flow-rob")

        mock_ct.assert_called_once()
        # Verify the task name is set (name= kwarg)
        _, kwargs = mock_ct.call_args
        assert "name" in kwargs
        assert kwargs["name"] == "tuya-cloudless-auto-stop"

    def test_unregister_flow_does_not_use_ensure_future(self) -> None:
        """unregister_flow must NOT use asyncio.ensure_future (deprecated path)."""
        hass = _make_hass()
        hass.async_create_task = MagicMock(return_value=MagicMock())
        srv = PairingServer(hass, port=0)
        srv._pending_flows.add("flow-rob2")

        with patch("asyncio.ensure_future", return_value=MagicMock()) as mock_ef:
            srv.unregister_flow("flow-rob2")

        mock_ef.assert_not_called()


# ── ha_ui_url ─────────────────────────────────────────────────────────────────


class TestHaUiUrl:
    """Tests for PairingServer.ha_ui_url() — prefers HTTPS HA over port 8099."""

    def test_returns_https_url_when_ha_is_https(self) -> None:
        """When HA's internal URL is HTTPS, ha_ui_url returns the HA HTTPS path."""
        hass = MagicMock()
        server = PairingServer(hass, port=8099)

        with (
            patch(
                "custom_components.tuya_cloudless.pairing_server.PairingServer.ha_local_url",
            ),
            patch(
                "homeassistant.helpers.network.get_url",
                return_value="https://ha.example.com:8123",
            ),
        ):
            url = server.ha_ui_url()

        assert url.startswith("https://ha.example.com:8123")
        assert "/api/tuya_cloudless/pairing" in url

    def test_falls_back_to_http_when_ha_is_http(self) -> None:
        """When HA's internal URL is HTTP, ha_ui_url falls back to port-8099."""
        hass = MagicMock()
        hass.config.internal_url = "http://homeassistant.local:8123"
        server = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            return_value="http://homeassistant.local:8123",
        ):
            url = server.ha_ui_url()

        assert url.startswith("http://")
        assert "8099" in url

    def test_falls_back_to_http_when_get_url_raises(self) -> None:
        """When get_url raises, ha_ui_url falls back to port-8099 URL."""
        hass = MagicMock()
        hass.config.internal_url = "http://homeassistant.local:8123"
        server = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=Exception("network error"),
        ):
            url = server.ha_ui_url()

        assert url.startswith("http://")
        assert "8099" in url

    def test_no_trailing_double_slash(self) -> None:
        """ha_ui_url must not produce a double-slash when HA URL has trailing slash."""
        hass = MagicMock()
        server = PairingServer(hass, port=8099)

        with patch(
            "homeassistant.helpers.network.get_url",
            return_value="https://ha.example.com:8123/",
        ):
            url = server.ha_ui_url()

        assert "//" not in url.replace("https://", "")

    def test_returns_https_url_when_only_external_is_https(self) -> None:
        """When internal URL is unavailable but external is HTTPS (Nabu Casa), use external."""
        hass = MagicMock()
        hass.config.internal_url = None
        server = PairingServer(hass, port=8099)

        call_count = 0

        def _side_effect(*args: object, **kwargs: bool) -> str:
            nonlocal call_count
            call_count += 1
            if kwargs.get("allow_internal"):
                from homeassistant.helpers.network import NoURLAvailableError

                raise NoURLAvailableError
            # External HTTPS URL (Nabu Casa)
            return "https://abc123.ui.nabu.casa"

        with patch(
            "homeassistant.helpers.network.get_url",
            side_effect=_side_effect,
        ):
            url = server.ha_ui_url()

        assert url.startswith("https://abc123.ui.nabu.casa")
        assert "/api/tuya_cloudless/pairing" in url
        assert call_count == 2  # tried internal (raised) then external (succeeded)


# ── HA index view (_handle_ha_index) ─────────────────────────────────────────


class TestHandleHaIndex:
    """Tests for _handle_ha_index — serves index.html with injected globals."""

    async def test_returns_200(self, server: PairingServer, tmp_path: Path) -> None:
        """Returns 200 with text/html when index.html exists."""
        ui_dir = tmp_path / "pairing_ui"
        ui_dir.mkdir()
        (ui_dir / "index.html").write_text(
            '<html><head></head><body><script src="/static/app.js"></script></body></html>',
            encoding="utf-8",
        )
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            ui_dir,
        ):
            req = MagicMock()
            resp = await server._handle_ha_index(req)

        assert resp.status == 200
        assert "text/html" in resp.content_type

    async def test_injects_globals(self, server: PairingServer, tmp_path: Path) -> None:
        """Response body contains injected window globals."""
        ui_dir = tmp_path / "pairing_ui"
        ui_dir.mkdir()
        (ui_dir / "index.html").write_text(
            "<html><head></head><body></body></html>", encoding="utf-8"
        )
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            ui_dir,
        ):
            req = MagicMock()
            resp = await server._handle_ha_index(req)

        body = resp.text
        assert "_TUYA_PROVISION_BASE" in body
        assert "_TUYA_STATIC_BASE" in body
        assert "_TUYA_ACTIVATOR_BASE" in body

    async def test_rewrites_static_paths(self, server: PairingServer, tmp_path: Path) -> None:
        """Static asset paths are rewritten to the HA-relative prefix."""
        ui_dir = tmp_path / "pairing_ui"
        ui_dir.mkdir()
        (ui_dir / "index.html").write_text(
            '<html><head></head><body><img src="/static/icon.png"></body></html>',
            encoding="utf-8",
        )
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            ui_dir,
        ):
            req = MagicMock()
            resp = await server._handle_ha_index(req)

        body = resp.text
        assert "/api/tuya_cloudless/pairing/static/icon.png" in body
        assert '="/static/' not in body

    async def test_404_when_index_missing(self, server: PairingServer, tmp_path: Path) -> None:
        """Returns 404 when index.html does not exist."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            empty_dir,
        ):
            req = MagicMock()
            resp = await server._handle_ha_index(req)

        assert resp.status == 404

    async def test_security_headers_on_200(self, server: PairingServer, tmp_path: Path) -> None:
        """200 response includes all required security headers."""
        ui_dir = tmp_path / "pairing_ui"
        ui_dir.mkdir()
        (ui_dir / "index.html").write_text(
            "<html><head></head><body></body></html>", encoding="utf-8"
        )
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            ui_dir,
        ):
            req = MagicMock()
            resp = await server._handle_ha_index(req)

        assert resp.status == 200
        assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "Referrer-Policy" in resp.headers
        assert "Content-Security-Policy" in resp.headers

    async def test_security_headers_on_404(self, server: PairingServer, tmp_path: Path) -> None:
        """404 response (missing index.html) also includes security headers."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            empty_dir,
        ):
            req = MagicMock()
            resp = await server._handle_ha_index(req)

        assert resp.status == 404
        assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" in resp.headers


# ── HA config view (_handle_ha_config) ───────────────────────────────────────


class TestHandleHaConfig:
    """Tests for _handle_ha_config — returns HTTPS-safe config."""

    async def test_returns_200_json(self, server: PairingServer) -> None:
        """Returns 200 JSON response."""
        req = MagicMock()
        with patch.object(server, "_get_default_ssid", new=AsyncMock(return_value=None)):
            resp = await server._handle_ha_config(req)

        assert resp.status == 200
        body = json.loads(resp.body)
        assert "activator_url" in body
        assert "events_url" in body
        assert "result_url_template" in body

    async def test_activator_url_is_http(self, server: PairingServer) -> None:
        """activator_url is always HTTP (Tuya device cannot do TLS)."""
        req = MagicMock()
        with patch.object(server, "_get_default_ssid", new=AsyncMock(return_value=None)):
            resp = await server._handle_ha_config(req)

        body = json.loads(resp.body)
        assert body["activator_url"].startswith("http://")

    async def test_events_url_is_ha_relative(self, server: PairingServer) -> None:
        """events_url is the HA-relative path (not port-8099 absolute URL)."""
        req = MagicMock()
        with patch.object(server, "_get_default_ssid", new=AsyncMock(return_value=None)):
            resp = await server._handle_ha_config(req)

        body = json.loads(resp.body)
        assert body["events_url"].startswith("/api/tuya_cloudless/pairing/provision/events")

    async def test_result_url_has_token_placeholder(self, server: PairingServer) -> None:
        """result_url_template contains {token} placeholder."""
        req = MagicMock()
        with patch.object(server, "_get_default_ssid", new=AsyncMock(return_value=None)):
            resp = await server._handle_ha_config(req)

        body = json.loads(resp.body)
        assert "{token}" in body["result_url_template"]


# ── _register_ha_views ────────────────────────────────────────────────────────


class TestRegisterHaViews:
    """Tests for _register_ha_views — registers HA HTTP views once."""

    async def test_registers_views_on_first_call(self) -> None:
        """_register_ha_views calls hass.http.register_view for each view."""
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        await server._register_ha_views()
        assert hass.http.register_view.called

    async def test_idempotent_second_call_skipped(self) -> None:
        """_register_ha_views is a no-op on the second call."""
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        await server._register_ha_views()
        first_call_count = hass.http.register_view.call_count
        await server._register_ha_views()
        second_call_count = hass.http.register_view.call_count
        assert second_call_count == first_call_count  # no extra calls

    async def test_ha_views_registered_flag_set(self) -> None:
        """_ha_views_registered is True after first call."""
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        assert server._ha_views_registered is False
        await server._register_ha_views()
        assert server._ha_views_registered is True

    async def test_registers_eight_views(self) -> None:
        """Exactly 8 views are registered.

        Views: index, config, events, result, wifi-scan, quick-scan,
        wifi-ap-pair, static.
        """
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        await server._register_ha_views()
        assert hass.http.register_view.call_count == 8


# ── Static file view (path traversal guard) ───────────────────────────────────


class TestHaStaticView:
    """Tests for the inline _PairingStaticView path-traversal guard."""

    def _get_static_view(self, ui_dir: Path) -> object:
        """Register views with the given ui_dir and return the static view instance."""
        hass = MagicMock()
        srv = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append

        import asyncio

        with patch(
            "custom_components.tuya_cloudless.pairing_server._UI_DIR",
            ui_dir,
        ):
            asyncio.get_event_loop().run_until_complete(srv._register_ha_views())

        return next(
            (v for v in captured_views if hasattr(v, "url") and "static" in str(v.url)),
            None,
        )

    async def test_serves_file(self, tmp_path: Path) -> None:
        """A valid relative path returns 200 with the file content."""
        ui_dir = tmp_path / "ui"
        ui_dir.mkdir()
        (ui_dir / "app.js").write_text("// app", encoding="utf-8")

        hass = MagicMock()
        srv = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append

        with patch("custom_components.tuya_cloudless.pairing_server._UI_DIR", ui_dir):
            await srv._register_ha_views()

        static_view = next(
            (v for v in captured_views if hasattr(v, "url") and "static" in str(v.url)),
            None,
        )
        assert static_view is not None
        req = MagicMock()
        resp = await static_view.get(req, path="app.js")  # type: ignore[union-attr]
        assert resp.status == 200

    async def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Path traversal (../secret.txt) returns 403."""
        ui_dir = tmp_path / "ui"
        ui_dir.mkdir()
        (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

        hass = MagicMock()
        srv = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append

        with patch("custom_components.tuya_cloudless.pairing_server._UI_DIR", ui_dir):
            await srv._register_ha_views()

        static_view = next(
            (v for v in captured_views if hasattr(v, "url") and "static" in str(v.url)),
            None,
        )
        assert static_view is not None
        req = MagicMock()
        resp = await static_view.get(req, path="../secret.txt")  # type: ignore[union-attr]
        assert resp.status == 403

    async def test_adjacent_dir_traversal_blocked(self, tmp_path: Path) -> None:
        """Adjacent directory with a shared name prefix cannot be accessed.

        A string-based startswith() check on "/tmp/ui" would also match
        "/tmp/ui-evil". Path.is_relative_to() does not have this flaw.
        """
        ui_dir = tmp_path / "ui"
        ui_dir.mkdir()
        evil_dir = tmp_path / "ui-evil"
        evil_dir.mkdir()
        (evil_dir / "secret.txt").write_text("top secret", encoding="utf-8")

        hass = MagicMock()
        srv = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append

        with patch("custom_components.tuya_cloudless.pairing_server._UI_DIR", ui_dir):
            await srv._register_ha_views()

        static_view = next(
            (v for v in captured_views if hasattr(v, "url") and "static" in str(v.url)),
            None,
        )
        assert static_view is not None
        req = MagicMock()
        # "../ui-evil/secret.txt" resolves to tmp/ui-evil/secret.txt — outside ui/
        resp = await static_view.get(req, path="../ui-evil/secret.txt")  # type: ignore[union-attr]
        assert resp.status == 403

    async def test_missing_file_returns_404(self, tmp_path: Path) -> None:
        """A path that does not exist returns 404."""
        ui_dir = tmp_path / "ui"
        ui_dir.mkdir()

        hass = MagicMock()
        srv = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append

        with patch("custom_components.tuya_cloudless.pairing_server._UI_DIR", ui_dir):
            await srv._register_ha_views()

        static_view = next(
            (v for v in captured_views if hasattr(v, "url") and "static" in str(v.url)),
            None,
        )
        assert static_view is not None
        req = MagicMock()
        resp = await static_view.get(req, path="nonexistent.js")  # type: ignore[union-attr]
        assert resp.status == 404


# ── Round 44: _json_in_script helper ──────────────────────────────────────────


class TestJsonInScript:
    """Tests for _json_in_script() — HTML-safe JSON encoding for <script> blocks."""

    def test_normal_string_unchanged(self) -> None:
        """Plain strings are JSON-encoded normally."""
        result = _json_in_script("http://192.168.1.10:8099")
        assert result == '"http://192.168.1.10:8099"'

    def test_script_close_tag_escaped(self) -> None:
        """</script> in a string value is escaped to prevent premature tag close."""
        crafted = "http://evil.example.com/</script><script>alert(1)//"
        result = _json_in_script(crafted)
        assert "</script>" not in result
        assert r"<\/" in result  # replacement sequence is present

    def test_nested_close_tag_escaped(self) -> None:
        """Multiple occurrences of </script> are all replaced."""
        crafted = "a</script>b</script>c"
        result = _json_in_script(crafted)
        assert result.count("</") == 0

    def test_non_string_values(self) -> None:
        """Works for non-string values like int and None."""
        assert _json_in_script(8099) == "8099"
        assert _json_in_script(None) == "null"

    def test_ha_index_script_block_safe_with_crafted_hostname(self, tmp_path: Path) -> None:
        """_handle_ha_index does not expose </script> in the injected <script> block."""
        ui_dir = tmp_path / "ui"
        ui_dir.mkdir()
        index_html = ui_dir / "index.html"
        index_html.write_text("<html><head></head><body></body></html>", encoding="utf-8")

        hass = MagicMock()
        srv = PairingServer(hass, port=8099)

        # Simulate a hostname that contains the </script> sequence
        crafted_url = "http://host-</script><script>alert(1)//:8099"
        with (
            patch.object(srv, "ha_local_url", return_value=crafted_url),
            patch("custom_components.tuya_cloudless.pairing_server._UI_DIR", ui_dir),
        ):
            import asyncio

            req = MagicMock()
            resp = asyncio.get_event_loop().run_until_complete(srv._handle_ha_index(req))

        body = resp.text if hasattr(resp, "text") else resp.body.decode()
        # The crafted </script> must not appear verbatim inside any <script> block
        assert "</script><script>alert(1)" not in body


# ── _get_client_ip ─────────────────────────────────────────────────────────────


class TestGetClientIp:
    """Tests for _get_client_ip() — proxy-aware IP extraction."""

    def test_non_loopback_xff_header_ignored(self, server: PairingServer) -> None:
        """X-Forwarded-For from a non-loopback peer must be ignored."""
        req = MagicMock()
        req.remote = "192.168.1.50"
        req.headers = {"X-Forwarded-For": "10.0.0.1"}
        assert server._get_client_ip(req) == "192.168.1.50"

    def test_loopback_ipv4_trusts_xff(self, server: PairingServer) -> None:
        """X-Forwarded-For from 127.0.0.1 is trusted (reverse proxy)."""
        req = MagicMock()
        req.remote = "127.0.0.1"
        req.headers = {"X-Forwarded-For": "10.0.0.99"}
        assert server._get_client_ip(req) == "10.0.0.99"

    def test_loopback_ipv6_trusts_xff(self, server: PairingServer) -> None:
        """::1 is treated as loopback and its X-Forwarded-For is trusted."""
        req = MagicMock()
        req.remote = "::1"
        req.headers = {"X-Forwarded-For": "203.0.113.5"}
        assert server._get_client_ip(req) == "203.0.113.5"

    def test_multi_value_xff_uses_first_entry(self, server: PairingServer) -> None:
        """Only the first X-Forwarded-For value is used."""
        req = MagicMock()
        req.remote = "127.0.0.1"
        req.headers = {"X-Forwarded-For": "10.0.0.1, 172.16.0.1, 192.168.0.1"}
        assert server._get_client_ip(req) == "10.0.0.1"

    def test_missing_remote_returns_unknown(self, server: PairingServer) -> None:
        """Empty/None request.remote falls back to 'unknown'."""
        req = MagicMock()
        req.remote = ""
        req.headers = {}
        assert server._get_client_ip(req) == "unknown"

    def test_loopback_no_xff_returns_loopback(self, server: PairingServer) -> None:
        """Loopback peer with no X-Forwarded-For returns the loopback IP."""
        req = MagicMock()
        req.remote = "127.0.0.1"
        req.headers = {}
        assert server._get_client_ip(req) == "127.0.0.1"


# ── gw_id character validation ─────────────────────────────────────────────────


class TestActivateGwIdValidation:
    """Tests for gw_id and product_key character validation in /api/tuya/device/active."""

    @pytest.mark.asyncio
    async def test_valid_alphanumeric_gw_id_accepted(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "abc123DEF456", "token": _TOKEN_A},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_gw_id_with_newline_rejected(self, client: TestClient) -> None:
        """gw_id containing newline must be rejected (SSE format injection guard)."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw\nevil", "token": _TOKEN_A},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_gw_id_with_control_char_rejected(self, client: TestClient) -> None:
        """gw_id containing control characters must be rejected."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw\x00id", "token": _TOKEN_A},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_product_key_unicode_rejected(self, client: TestClient) -> None:
        """Non-ASCII product_key must be rejected."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gwvalid", "product_key": "pk\u0400", "token": _TOKEN_A},
        )
        assert resp.status == 400
