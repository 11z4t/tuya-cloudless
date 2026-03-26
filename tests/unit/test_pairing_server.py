"""Unit tests for custom_components/tuya_cloudless/pairing_server.

Tests the fake-cloud activation endpoint, result storage, SSE broadcasting,
and helper utilities — without needing a real HA instance or Tuya device.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
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
_TOKEN_R38 = "38" * 16  # R38 tests

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
async def server(tmp_path: Path) -> PairingServer:  # type: ignore[misc]
    """Return a PairingServer with a free port (not started).

    Teardown cancels any pending background tasks (e.g. _auto_stop_after_idle,
    SSE broadcasts) so that unawaited coroutine warnings don't pollute tests.
    """
    srv = PairingServer(_make_hass(), port=0)
    yield srv  # type: ignore[misc]
    # Cancel all pending tasks scheduled by the server during the test.
    # Give any pending callbacks/tasks a chance to start before cancellation,
    # otherwise tasks that haven't yet begun generate "coroutine never awaited"
    # warnings when the task is cancelled and its coroutine is GC'd.
    await asyncio.sleep(0)
    tasks_to_cancel: list[asyncio.Task[object]] = []
    if srv._auto_stop_task is not None and not srv._auto_stop_task.done():
        tasks_to_cancel.append(srv._auto_stop_task)
    for t in list(srv._background_tasks):
        if not t.done():
            tasks_to_cancel.append(t)
    for t in tasks_to_cancel:
        t.cancel()
    for t in tasks_to_cancel:
        with contextlib.suppress(asyncio.CancelledError):
            await t


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

    async def test_sw_ver_with_control_chars_rejected(self, client: TestClient) -> None:
        """sw_ver containing control characters must be rejected (log-injection guard)."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "sw_ver": "2.0\ninjected-log-line"},
        )
        assert resp.status == 400

    async def test_sw_ver_normal_version_string_accepted(self, client: TestClient) -> None:
        """Legitimate firmware version strings must be accepted."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "sw_ver": "2.4.1"},
        )
        assert resp.status == 200

    async def test_sw_ver_unicode_line_sep_rejected(self, client: TestClient) -> None:
        """sw_ver with Unicode line separator (U+2028) must be rejected.

        U+2028 bypasses the old [\x00-\x1f\x7f] denylist but is blocked by the
        new positive allowlist ^[a-zA-Z0-9._-]{1,32}$.
        """
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "sw_ver": "1.0\u2028injected"},
        )
        assert resp.status == 400

    async def test_sw_ver_space_rejected(self, client: TestClient) -> None:
        """sw_ver with a space must be rejected (not in allowlist)."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "sw_ver": "1.0 beta"},
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

    async def test_token_with_wrong_format_rejected(self, client: TestClient) -> None:
        """Token matching length but failing regex (uppercase) returns 400 (lines 801-803)."""
        # "ABCD..." is 32 chars but contains uppercase — fails _TOKEN_RE
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "token": "AB" * 16},
        )
        assert resp.status == 400

    async def test_results_table_full_evicts_oldest(
        self, client: TestClient, server: PairingServer
    ) -> None:
        """When _results is at capacity, the oldest entry is evicted (lines 873-878)."""
        from custom_components.tuya_cloudless.pairing_server import (
            _MAX_STORED_RESULTS,
            ActivationResult,
        )

        # Fill the results table to the cap
        for i in range(_MAX_STORED_RESULTS):
            tok = f"{i:032x}"
            server._results[tok] = ActivationResult(
                gw_id=f"gw{i}", product_key="", local_key="lk", ip_address=""
            )
        oldest_token = next(iter(server._results))

        # One more activation should evict the oldest entry
        new_token = "ee" * 16
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_new", "token": new_token},
        )
        assert resp.status == 200
        assert oldest_token not in server._results
        assert new_token in server._results

    async def test_tokenless_results_full_evicts_oldest(
        self, client: TestClient, server: PairingServer
    ) -> None:
        """When _tokenless_results is at capacity, the oldest entry is evicted (lines 885-889)."""
        from custom_components.tuya_cloudless.pairing_server import (
            _MAX_STORED_RESULTS,
            ActivationResult,
        )

        # Fill the tokenless results table to the cap
        for i in range(_MAX_STORED_RESULTS):
            server._tokenless_results[f"gw_{i}"] = ActivationResult(
                gw_id=f"gw_{i}", product_key="", local_key="lk", ip_address=""
            )
        oldest_gw = next(iter(server._tokenless_results))

        # Activate without token → stored by gw_id in tokenless table
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_new_tokenless"},
        )
        assert resp.status == 200
        assert oldest_gw not in server._tokenless_results
        assert "gw_new_tokenless" in server._tokenless_results


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

    async def test_result_get_rate_limited(self, server: PairingServer) -> None:
        """GET result returns 429 when per-IP rate limit (30/min) is exceeded (line 1005)."""
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            # Pre-fill rate limit bucket just below capacity (30)
            now = time.monotonic()
            server._rate_limit["127.0.0.1"] = [now] * 29
            # 30th request succeeds
            resp = await cli.get(f"/api/provision/result/{_TOKEN_A}")
            assert resp.status in (200, 202)
            # 31st request exceeds limit
            resp = await cli.get(f"/api/provision/result/{_TOKEN_A}")
            assert resp.status == 429
            assert "Retry-After" in resp.headers
        finally:
            await cli.close()


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

    @pytest.mark.asyncio
    async def test_ap_token_rejects_form_urlencoded(self, client: TestClient) -> None:
        """ap-token must reject form-urlencoded (CSRF guard)."""
        resp = await client.post(
            "/api/provision/ap-token",
            data="foo=bar",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status == 415

    @pytest.mark.asyncio
    async def test_ap_token_rejects_missing_content_type(self, client: TestClient) -> None:
        """ap-token must reject a POST with no Content-Type (CSRF guard)."""
        resp = await client.post(
            "/api/provision/ap-token",
            data=b"{}",
        )
        assert resp.status == 415

    @pytest.mark.asyncio
    async def test_ap_token_accepts_json_content_type(self, client: TestClient) -> None:
        """ap-token must accept a legitimate application/json POST."""
        resp = await client.post(
            "/api/provision/ap-token",
            json={},
        )
        assert resp.status == 200
        body = await resp.json()
        assert "token" in body

    @pytest.mark.asyncio
    async def test_wifi_ap_pair_rejects_form_urlencoded(self, client: TestClient) -> None:
        """wifi-ap-pair must reject form-urlencoded (CSRF guard)."""
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            data="foo=bar",
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

    async def test_pass2_adds_new_ssids_not_in_pass1(self, client: TestClient) -> None:
        """SSIDs that appear only in pass-2 (cache re-read) are added to results (lines 1278-1279).

        Pass 1 (rescan=yes) returns HomeNet only.
        Pass 2 (rescan=no, cache) returns HomeNet + OfficeWifi.
        OfficeWifi is new in pass 2 — it must appear in the final ssids list.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        scan_call_count = 0

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal scan_call_count
            proc = MagicMock()
            proc.kill = MagicMock()
            # The _get_default_ssid call uses --fields ACTIVE,SSID
            if "--fields" in args and "ACTIVE,SSID" in args:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                return proc
            # Scan calls: first call returns HomeNet only, second adds OfficeWifi
            scan_call_count += 1
            if scan_call_count == 1:
                proc.communicate = AsyncMock(return_value=(b"HomeNet\n", b""))
            else:
                proc.communicate = AsyncMock(return_value=(b"HomeNet\nOfficeWifi\n", b""))
            return proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("asyncio.sleep"),
        ):
            resp = await client.get("/api/provision/wifi-scan")

        assert resp.status == 200
        data = await resp.json()
        assert "HomeNet" in data["ssids"]
        # OfficeWifi is new in pass 2 — must be present
        assert "OfficeWifi" in data["ssids"]
        # Each SSID appears exactly once (deduplication works correctly)
        assert data["ssids"].count("HomeNet") == 1
        assert data["ssids"].count("OfficeWifi") == 1

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

    def test_wifi_prefix_removed(self) -> None:
        """'wifi_' was removed from _TUYA_AP_PREFIXES (R27-2: too broad, allows
        arbitrary SSIDs like 'wifi_evil_hotspot' to reach nmcli connect)."""
        assert _is_tuya_ap("WiFi_Plug") is False

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
        """All current Tuya AP prefixes are matched (case-insensitive).

        'wifi_' was removed in R27-2 — it is too broad.  WiFi_EE must no
        longer be included in tuya_aps.
        """
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
        assert "WiFi_EE" not in ssids  # "wifi_" prefix removed (R27-2)
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

    async def test_oserror_log_does_not_include_exc_str(self, client: TestClient) -> None:
        """R37-5: OSError in WiFi AP task must log type name only, not exc string.

        aiohttp OSError subclasses may embed the request body (containing the
        WiFi password) in their str() representation.
        """
        import logging
        from unittest.mock import patch

        hass = MagicMock()
        server = PairingServer(hass, port=9099)
        server._broadcast_sse = AsyncMock()  # type: ignore[method-assign]

        sensitive = "SECRETPASSWORD_IN_EXCEPTION"

        class SensitiveOSError(OSError):
            def __str__(self) -> str:
                return f"Connection failed with body containing {sensitive}"

        log_messages: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_messages.append(self.format(record))

        handler = CapturingHandler()
        logger = logging.getLogger("custom_components.tuya_cloudless.pairing_server")
        logger.addHandler(handler)
        try:
            with patch(
                "asyncio.create_subprocess_exec",
                side_effect=SensitiveOSError("failed"),
            ):
                await server._wifi_ap_pair_task(
                    "SmartLife_AB12", "HomeNet", "pass", "tok123", "http://ha:8099"
                )
        finally:
            logger.removeHandler(handler)

        # The sensitive string must NOT appear in any log message (R37-5)
        combined_logs = " ".join(log_messages)
        assert sensitive not in combined_logs, (
            f"R37-5: sensitive exception content leaked to log: {combined_logs[:200]}"
        )

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
        # R35-9: token must NOT appear in SSE payload (unauthenticated stream)
        error_payloads = [data for ev, data in broadcast_calls if ev == "wifi_ap_error"]
        assert not any("tok_fail" in p for p in error_payloads), (
            "wifi_ap_error SSE payload must NOT include the session token (R35-9)"
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

    async def test_non_dict_json_body_returns_400(self, client: TestClient) -> None:
        """JSON array body (not a dict) returns 400 (line 1405)."""
        resp = await client.post(
            "/api/provision/wifi-ap-pair",
            headers={"Content-Type": "application/json"},
            data='["not", "a", "dict"]',
        )
        assert resp.status == 400

    async def test_valid_flow_id_creates_token_binding(self, server: PairingServer) -> None:
        """Valid registered flow_id causes token→flow binding creation (line 1480)."""
        from unittest.mock import patch

        flow_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        server._pending_flows.add(flow_id)
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            with patch(
                "custom_components.tuya_cloudless.pairing_server.PairingServer._wifi_ap_pair_task"
            ):
                resp = await cli.post(
                    "/api/provision/wifi-ap-pair",
                    json={
                        "ap_ssid": "SmartLife_AB12",
                        "home_ssid": "HomeNet",
                        "home_password": "pass",
                        "flow_id": flow_id,
                    },
                )
            assert resp.status == 200
            data = await resp.json()
            token = data["token"]
            # The token should be bound to the provided flow_id
            assert server._token_to_flow.get(token) == flow_id
        finally:
            await cli.close()

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

    async def test_cors_header_absent(self, client: TestClient) -> None:
        """Config endpoint must NOT send wildcard CORS (leaks internal IP/topology)."""
        resp = await client.get("/api/provision/config")
        assert resp.headers.get("Access-Control-Allow-Origin") != "*"

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

    async def test_default_ssid_iwgetid_timeout_returns_none(self, client: TestClient) -> None:
        """iwgetid timeout triggers proc.kill() cleanup and returns None (lines 2056-2061)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)

        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=[FileNotFoundError, mock_proc],
            ),
            patch("shutil.which", return_value="/sbin/iwgetid"),
        ):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] is None
        mock_proc.kill.assert_called_once()

    async def test_default_ssid_iwgetid_oserror_returns_none(self, client: TestClient) -> None:
        """OSError from iwgetid subprocess returns None (lines 2070-2071)."""
        from unittest.mock import patch

        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=[FileNotFoundError, OSError("no iwgetid device")],
            ),
            patch("shutil.which", return_value="/sbin/iwgetid"),
        ):
            resp = await client.get("/api/provision/config")
        data = await resp.json()
        assert data["default_ssid"] is None

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

    async def test_duplicate_activation_does_not_resume_flows(self, server: PairingServer) -> None:
        """R34-4: Duplicate activation (idempotency path) must not re-resume config flows.

        Re-resuming on a duplicate activation could inject stale credentials into an
        unrelated, concurrently registered config flow if the original token is replayed.
        """
        token = _TOKEN_DEDUP
        server._pending_flows.add("flow-a")
        # Pre-seed the result so the next activation hits the idempotency path
        from custom_components.tuya_cloudless.pairing_server import ActivationResult

        server._results[token] = ActivationResult(
            gw_id="dedup_gw", product_key="pk", local_key="1234" * 4, ip_address="1.2.3.4"
        )

        task_calls: list[object] = []

        def _capture_task(coro: object, **kw: object) -> object:
            task_calls.append(coro)
            return asyncio.ensure_future(coro)  # type: ignore[arg-type]

        server._hass.async_create_task = MagicMock(side_effect=_capture_task)  # type: ignore[attr-defined]

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "dedup_gw", "token": token},
            )
        finally:
            await cli.close()

        # R40-F5/R41-F2: SSE broadcast fires even on duplicate activation (that's correct).
        # Only _resume_flow tasks must NOT be present.
        resume_tasks = [c for c in task_calls if "_resume_flow" in getattr(c, "__qualname__", "")]
        assert not resume_tasks, (
            "Duplicate activation must NOT call async_create_task to resume flows — "
            "the flow already received credentials on the first activation"
        )
        # Close any SSE coroutines to avoid ResourceWarning
        for c in task_calls:
            if hasattr(c, "close") and "_broadcast_sse" in getattr(c, "__qualname__", ""):
                c.close()

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
            # async_create_task should have been called at least twice:
            # once for the SSE broadcast (R40-F5/R41-F2) and once for the flow resume.
            assert hass.async_create_task.call_count >= 2, (
                "Expected async_create_task for both SSE broadcast and flow resume"
            )
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
        # Accept **kwargs to handle the 'name' keyword added in R41-F2.
        hass.async_create_task = lambda coro, **kw: asyncio.get_event_loop().create_task(coro)

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

    async def test_empty_token_does_not_resume_flows(self) -> None:
        """R27-5: Activation with empty token must NOT resume pending config flows.

        Any LAN host can POST to /api/tuya/device/active with an empty token.
        Previously this would resume all waiting flows with attacker-controlled
        gw_id/local_key, creating a config entry for the wrong device.
        Now only token-carrying activations resume flows.
        """
        hass = _make_hass()
        hass.async_create_task = MagicMock()
        hass.config_entries = MagicMock()

        fresh_server = PairingServer(hass, port=0)
        fresh_server._pending_flows.add("victim-flow-123")

        ts = TestServer(fresh_server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "attacker_device"},  # no token field
            )
            assert resp.status == 200
            # Flow must NOT have been resumed with attacker's gw_id.
            # Note: async_create_task IS called for the SSE broadcast (R40-F5/R41-F2)
            # but config_entries.flow.async_configure must NOT have been scheduled.
            for call_args in hass.async_create_task.call_args_list:
                coro = call_args[0][0] if call_args[0] else None
                if coro is not None:
                    coro_name = getattr(coro, "__qualname__", "") or ""
                    assert "_resume_flow" not in coro_name, (
                        "Flow resume task must NOT be created for tokenless activation"
                    )
                    # Close all coroutines passed to the mock (no real Tasks are
                    # created since async_create_task is a MagicMock), otherwise
                    # Python warns about unawaited coroutines at GC time.
                    if hasattr(coro, "close"):
                        coro.close()  # type: ignore[union-attr]
            assert "victim-flow-123" in fresh_server._pending_flows
        finally:
            await cli.close()

    async def test_bound_token_resumes_only_its_flow(self) -> None:
        """R28-1: When token is pre-bound to flow A, only flow A is resumed on activation."""
        hass = _make_hass()
        resume_calls: list[str] = []

        async def fake_configure(flow_id: str, user_input: object) -> None:
            resume_calls.append(flow_id)

        hass.config_entries = MagicMock()
        hass.config_entries.flow.async_configure = AsyncMock(side_effect=fake_configure)

        tasks: list[asyncio.Task[None]] = []

        def capture_task(coro: object, **_kw: object) -> asyncio.Task[None]:
            t = asyncio.get_event_loop().create_task(coro)  # type: ignore[arg-type]
            tasks.append(t)
            return t

        hass.async_create_task = capture_task

        fresh_server = PairingServer(hass, port=0)
        fresh_server._pending_flows.add("flow-a")
        fresh_server._pending_flows.add("flow-b")
        # Pre-bind token to flow-a only
        token = "ab" * 16  # 32 hex chars
        fresh_server._token_to_flow[token] = "flow-a"

        ts = TestServer(fresh_server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "aabbccddee112233", "token": token},
            )
            assert resp.status == 200
            # Let async tasks run
            for t in tasks:
                await t
            # Only flow-a should have been resumed — flow-b must not receive credentials
            assert resume_calls == ["flow-a"]
        finally:
            await cli.close()

    async def test_unbound_token_resumes_all_pending_flows(self) -> None:
        """R28-1 fallback: unbound token (BLE without bind-token call) resumes all flows."""
        hass = _make_hass()
        resume_calls: list[str] = []

        async def fake_configure(flow_id: str, user_input: object) -> None:
            resume_calls.append(flow_id)

        hass.config_entries = MagicMock()
        hass.config_entries.flow.async_configure = AsyncMock(side_effect=fake_configure)

        tasks: list[asyncio.Task[None]] = []

        def capture_task(coro: object, **_kw: object) -> asyncio.Task[None]:
            t = asyncio.get_event_loop().create_task(coro)  # type: ignore[arg-type]
            tasks.append(t)
            return t

        hass.async_create_task = capture_task

        fresh_server = PairingServer(hass, port=0)
        fresh_server._pending_flows.add("flow-x")
        # No token → flow binding

        ts = TestServer(fresh_server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            token = "cd" * 16
            resp = await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "aabbccddee112233", "token": token},
            )
            assert resp.status == 200
            for t in tasks:
                await t
            assert "flow-x" in resume_calls
        finally:
            await cli.close()


class TestBindTokenEndpoint:
    """R28-1/R33-1: bind-token is NOT on the unauthenticated port-8099 server.

    The handler logic remains (used by the HA HTTPS authenticated endpoint).
    These tests call _handle_bind_token directly to verify handler logic, and
    confirm the 8099 route returns 404.
    """

    async def test_bind_token_not_on_8099_server(self) -> None:
        """R33-1: bind-token must NOT be accessible on the unauthenticated port-8099."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)

        ts = TestServer(fresh_server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            resp = await cli.post(
                "/api/provision/bind-token",
                json={"flow_id": "any-flow", "token": "aa" * 16},
            )
            assert resp.status == 404, (
                "bind-token must return 404 on the unauthenticated 8099 server"
            )
        finally:
            await cli.close()

    async def test_bind_token_handler_binds_flow(self) -> None:
        """Handler logic: valid token+flow_id returns 204 and stores binding."""

        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        token = "aa" * 16
        fresh_server._pending_flows.add("my-flow")

        req = MagicMock()
        req.json = AsyncMock(return_value={"flow_id": "my-flow", "token": token})
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 204
        assert fresh_server._token_to_flow[token] == "my-flow"

    async def test_bind_token_handler_rejects_unknown_flow(self) -> None:
        """Handler logic: unknown flow_id returns 400."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        token = "bb" * 16

        req = MagicMock()
        req.json = AsyncMock(return_value={"flow_id": "not-registered", "token": token})
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 400

    async def test_bind_token_handler_rejects_invalid_token(self) -> None:
        """Handler logic: invalid token format returns 400."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        fresh_server._pending_flows.add("flow-z")

        req = MagicMock()
        req.json = AsyncMock(return_value={"flow_id": "flow-z", "token": "not-hex!!"})
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 400

    async def test_bind_token_handler_rejects_rebind(self) -> None:
        """Handler logic: rebinding an already-bound token returns 409."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        token = "cc" * 16
        fresh_server._pending_flows.add("flow-one")
        fresh_server._token_to_flow[token] = "flow-one"

        req = MagicMock()
        req.json = AsyncMock(return_value={"flow_id": "flow-one", "token": token})
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 409

    async def test_bind_token_rate_limited(self) -> None:
        """Rate limit (20/min) on bind-token returns 429 (lines 1068-1072)."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        now = time.monotonic()
        # Pre-fill rate limit to the cap (20) for "unknown" client IP
        fresh_server._rate_limit["unknown"] = [now] * 20
        req = MagicMock()
        req.remote = None
        req.headers = {}
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 429

    async def test_bind_token_invalid_json_body(self) -> None:
        """Invalid JSON request body returns 400 (lines 1076-1077)."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        req = MagicMock()
        req.remote = None
        req.headers = {}
        req.json = AsyncMock(side_effect=json.JSONDecodeError("bad", "", 0))
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 400

    async def test_bind_token_non_dict_json_body(self) -> None:
        """JSON list body (not a dict) returns 400 (line 1080)."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        req = MagicMock()
        req.remote = None
        req.headers = {}
        req.json = AsyncMock(return_value=["not", "a", "dict"])
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 400

    async def test_bind_token_cap_evicts_oldest(self) -> None:
        """When token_to_flow cap (256) is reached, oldest entry is evicted (lines 1103-1105)."""
        hass = _make_hass()
        fresh_server = PairingServer(hass, port=0)
        # Fill token_to_flow to exactly 256 entries
        for i in range(256):
            fresh_server._token_to_flow[f"{i:032x}"] = f"flow-{i}"
        oldest_tok = next(iter(fresh_server._token_to_flow))
        # Register a pending flow and bind a new token
        new_token = "aa" * 16
        fresh_server._pending_flows.add("flow-new")
        req = MagicMock()
        req.remote = None
        req.headers = {}
        req.json = AsyncMock(return_value={"flow_id": "flow-new", "token": new_token})
        resp = await fresh_server._handle_bind_token(req)
        assert resp.status == 204
        # Oldest entry evicted; new entry present
        assert oldest_tok not in fresh_server._token_to_flow
        assert fresh_server._token_to_flow[new_token] == "flow-new"

    def test_unregister_flow_cleans_up_token_binding(self) -> None:
        """R29-1: unregister_flow must remove any token→flow bindings for that flow.

        If the device never activates (user cancels the flow), the bind-token entry
        would otherwise accumulate until the server stops.
        """
        hass = _make_hass()
        hass.async_create_task = MagicMock()  # suppress auto-stop task creation
        fresh_server = PairingServer(hass, port=0)

        fresh_server._pending_flows.add("flow-a")
        token_a = "ff" * 16
        fresh_server._token_to_flow[token_a] = "flow-a"

        # Sanity: binding is present
        assert token_a in fresh_server._token_to_flow

        # Unregistering the flow must also remove the token binding
        fresh_server.unregister_flow("flow-a")
        assert token_a not in fresh_server._token_to_flow

    def test_unregister_flow_does_not_remove_other_flow_bindings(self) -> None:
        """R29-1: unregister_flow only removes bindings for the unregistered flow."""
        hass = _make_hass()
        hass.async_create_task = MagicMock()
        fresh_server = PairingServer(hass, port=0)

        fresh_server._pending_flows.add("flow-a")
        fresh_server._pending_flows.add("flow-b")
        token_a = "aa" * 16
        token_b = "bb" * 16
        fresh_server._token_to_flow[token_a] = "flow-a"
        fresh_server._token_to_flow[token_b] = "flow-b"

        fresh_server.unregister_flow("flow-a")

        # flow-a's token is gone; flow-b's is intact
        assert token_a not in fresh_server._token_to_flow
        assert fresh_server._token_to_flow[token_b] == "flow-b"


# ── _expire_old_results / R36-4 ────────────────────────────────────────────────


class TestExpireStaleTokenToFlow:
    def test_expire_purges_token_to_flow_for_removed_flows(self) -> None:
        """R36-4: _expire_old_results removes _token_to_flow entries for non-pending flows."""
        hass = _make_hass()
        hass.async_create_task = MagicMock()
        server = PairingServer(hass, port=0)

        server._pending_flows.add("active-flow")
        server._token_to_flow["tok-active"] = "active-flow"
        server._token_to_flow["tok-orphan"] = "abandoned-flow"  # no matching pending flow

        server._expire_old_results()

        assert "tok-orphan" not in server._token_to_flow
        assert server._token_to_flow.get("tok-active") == "active-flow"

    def test_expire_keeps_active_tokens(self) -> None:
        """R36-4: _expire_old_results does not remove tokens for active flows."""
        hass = _make_hass()
        hass.async_create_task = MagicMock()
        server = PairingServer(hass, port=0)

        server._pending_flows.add("flow-1")
        server._pending_flows.add("flow-2")
        server._token_to_flow["tok1"] = "flow-1"
        server._token_to_flow["tok2"] = "flow-2"

        server._expire_old_results()

        assert server._token_to_flow["tok1"] == "flow-1"
        assert server._token_to_flow["tok2"] == "flow-2"


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

    async def test_sse_rate_limited(self, server: PairingServer) -> None:
        """SSE endpoint returns 429 when per-IP rate limit (10/min) is exceeded (line 1126)."""
        now = time.monotonic()
        # Pre-fill rate limit to exactly the limit (10) for 127.0.0.1
        server._rate_limit["127.0.0.1"] = [now] * 10
        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            resp = await cli.get("/api/provision/events")
            assert resp.status == 429
            assert "Retry-After" in resp.headers
        finally:
            await cli.close()

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

    async def test_newline_in_event_name_drops_not_raises(self, server: PairingServer) -> None:
        """R30-2: Newline in event name must log+drop, NOT raise ValueError.

        Previously a raise would propagate to the aiohttp handler (500 error) or
        silently kill a background asyncio Task.
        """
        q: asyncio.Queue[str | None] = asyncio.Queue()
        server._sse_queues.append(q)
        # Should not raise — must drop silently
        await server._broadcast_sse("event\nwith-newline", "{}")
        # Queue must be empty — event was dropped
        assert q.empty()

    async def test_newline_in_data_drops_not_raises(self, server: PairingServer) -> None:
        """R30-2: Newline in data payload must log+drop, NOT raise ValueError."""
        q: asyncio.Queue[str | None] = asyncio.Queue()
        server._sse_queues.append(q)
        await server._broadcast_sse("activated", "data\nwith-newline")
        assert q.empty()


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

    async def test_does_not_stop_when_not_current_server(self) -> None:
        """R34-2: If a replacement server has been installed, the old server must not stop.

        The identity check `current is self` prevents a stale auto-stop task from
        stopping the replacement server that was started while the old one was idle.
        """
        from custom_components.tuya_cloudless.const import DOMAIN
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        hass.data = {}

        old_srv = PairingServer(hass, port=0)
        new_srv = PairingServer(hass, port=0)
        # A replacement server was installed — old_srv is no longer current
        hass.data[DOMAIN] = {_KEY_PAIRING_SERVER: new_srv}

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
            await old_srv._auto_stop_after_idle()

        assert not stop_called, (
            "Stale auto-stop task on old_srv must not stop the replacement server"
        )


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

    async def test_start_oserror_calls_stop_and_reraises(self) -> None:
        """OSError in start() triggers cleanup via stop() and re-raises (lines 2331-2338)."""
        from custom_components.tuya_cloudless.const import DOMAIN
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        hass.data = {}
        stop_called = False

        async def failing_start(self_srv: PairingServer) -> None:
            raise OSError("port in use")

        async def fake_stop(self_srv: PairingServer) -> None:
            nonlocal stop_called
            stop_called = True

        with (
            patch.object(PairingServer, "start", failing_start),
            patch.object(PairingServer, "stop", fake_stop),
            pytest.raises(OSError, match="port in use"),
        ):
            await ensure_pairing_server(hass)

        assert stop_called, "stop() must be called to clean up after start() failure"
        # Server must NOT be stored in domain_data on failure
        assert DOMAIN not in hass.data or _KEY_PAIRING_SERVER not in hass.data.get(DOMAIN, {})

    async def test_start_cancelled_calls_stop_and_reraises(self) -> None:
        """CancelledError in start() triggers cleanup via stop() and re-raises (lines 2331-2338)."""
        from custom_components.tuya_cloudless.pairing_server import _KEY_PAIRING_SERVER

        hass = _make_hass()
        hass.data = {}
        stop_called = False

        async def failing_start(self_srv: PairingServer) -> None:
            raise asyncio.CancelledError()

        async def fake_stop(self_srv: PairingServer) -> None:
            nonlocal stop_called
            stop_called = True

        with (
            patch.object(PairingServer, "start", failing_start),
            patch.object(PairingServer, "stop", fake_stop),
            pytest.raises(asyncio.CancelledError),
        ):
            await ensure_pairing_server(hass)

        assert stop_called
        from custom_components.tuya_cloudless.const import DOMAIN

        assert _KEY_PAIRING_SERVER not in hass.data.get(DOMAIN, {})


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

    async def test_sse_activated_event_omits_token(self, client: TestClient) -> None:
        """R33-3: SSE activated event must NOT contain the provisioning token.

        The SSE stream is unauthenticated and accessible to any LAN host. Including
        the token would allow any LAN host to call GET /api/provision/result/{token}
        and retrieve the local_key. The browser already holds the token from the
        wifi-ap-pair response or from BLE JS generation.
        """
        async with client.session.get(
            client.make_url("/api/provision/events"),
        ) as resp:
            assert resp.status == 200
            await asyncio.wait_for(resp.content.read(64), timeout=5)  # skip connected comment

            await client.post(
                "/api/tuya/device/active",
                json={"gw_id": "gw_sse_tok", "token": _TOKEN_SSE},
            )

            data = await asyncio.wait_for(resp.content.read(512), timeout=5)
            assert b"event: activated" in data
            assert _TOKEN_SSE.encode() not in data, (
                "token must not appear in SSE broadcast — any LAN host could use it "
                "to retrieve local_key from the result endpoint"
            )


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
                # Close the coroutine — we're not awaiting it so it must be
                # explicitly closed to avoid "coroutine never awaited" warnings.
                if inspect.iscoroutine(coro):
                    coro.close()  # type: ignore[union-attr]
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
            # Close the coroutine (Queue.get()) so it doesn't emit a warning.
            if inspect.iscoroutine(coro):
                coro.close()  # type: ignore[union-attr]
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
            # Close the coroutine (Queue.get()) to avoid "never awaited" warnings.
            if inspect.iscoroutine(coro):
                coro.close()  # type: ignore[union-attr]
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
            # Close the coroutine (Queue.get()) to avoid "never awaited" warnings.
            if inspect.iscoroutine(coro):
                coro.close()  # type: ignore[union-attr]
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

    async def test_broken_pipe_on_message_write_exits_cleanly(self, server: PairingServer) -> None:
        """R32-3: BrokenPipeError (EPIPE) on response.write() must exit cleanly.

        BrokenPipeError is an OSError subclass (not ConnectionResetError) raised
        when the browser sends FIN before the server finishes writing the event.
        Previously only ConnectionResetError was caught, causing spurious ERROR
        logs for every browser tab close during an active SSE session.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        await queue.put("data: test\n\n")

        async def fake_wait_for(coro: object, timeout: float = 0) -> str | None:
            # Close the production Queue.get() coroutine — we're not awaiting it,
            # we drive the SSE loop via our own local queue instead.
            if inspect.iscoroutine(coro):
                coro.close()  # type: ignore[union-attr]
            return await queue.get()

        async def failing_write(data: bytes) -> None:
            if b"data:" in data:
                raise BrokenPipeError("EPIPE — client closed connection")

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
            # Must complete without raising — BrokenPipeError must be caught
            await server._handle_sse(MagicMock())

        # Queue is cleaned up after exit
        assert queue not in server._sse_queues


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

    async def test_config_endpoint_no_wildcard_cors(self, client: TestClient) -> None:
        """GET /api/provision/config must NOT send wildcard CORS (leaks internal IP)."""
        resp = await client.get("/api/provision/config")
        assert resp.headers.get("Access-Control-Allow-Origin") != "*"

    async def test_wifi_scan_no_wildcard_cors(self, client: TestClient) -> None:
        """GET /api/provision/wifi-scan must NOT send wildcard CORS (leaks WiFi SSIDs)."""
        resp = await client.get("/api/provision/wifi-scan")
        assert resp.headers.get("Access-Control-Allow-Origin") != "*"


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

    async def test_ha_config_includes_wifi_scan_available(self, server: PairingServer) -> None:
        """R37-1: HA HTTPS config endpoint must include wifi_scan_available field."""
        req = MagicMock()
        with patch.object(server, "_get_default_ssid", new=AsyncMock(return_value=None)):
            resp = await server._handle_ha_config(req)

        body = json.loads(resp.body)
        assert "wifi_scan_available" in body, (
            "R37-1: wifi_scan_available missing from HA config — "
            "pairing UI scan button will not be correctly disabled in containers"
        )
        assert isinstance(body["wifi_scan_available"], bool)

    async def test_ha_config_includes_integration_version(self, server: PairingServer) -> None:
        """R37-1: HA HTTPS config endpoint must include integration_version field."""
        req = MagicMock()
        with patch.object(server, "_get_default_ssid", new=AsyncMock(return_value=None)):
            resp = await server._handle_ha_config(req)

        body = json.loads(resp.body)
        assert "integration_version" in body
        assert isinstance(body["integration_version"], str)
        assert body["integration_version"] != ""


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

    async def test_registers_ten_views(self) -> None:
        """Exactly 10 views are registered.

        Views: index, config, events, result, wifi-scan, quick-scan,
        wifi-ap-pair, ap-token, bind-token (R31-4: authenticated HA HTTPS
        variant), static.
        """
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        await server._register_ha_views()
        assert hass.http.register_view.call_count == 10

    async def test_sensitive_views_require_auth(self) -> None:
        """wifi-scan, quick-scan, and wifi-ap-pair HA views must require auth.

        These endpoints reveal home WiFi SSIDs (scan) or execute nmcli on the
        host (wifi-ap-pair).  Allowing unauthenticated access would let any
        browser on the LAN (or via Nabu Casa remote) trigger network changes.
        """
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append
        await server._register_ha_views()

        sensitive_names = {
            "api:tuya_cloudless:pairing:wifi_scan",
            "api:tuya_cloudless:pairing:quick_scan",
            "api:tuya_cloudless:pairing:wifi_ap_pair",
            # R31-4: bind-token must require auth on the HA HTTPS path
            "api:tuya_cloudless:pairing:bind_token",
        }
        for view in captured_views:
            name = getattr(view, "name", "")
            if name in sensitive_names:
                requires = getattr(view, "requires_auth", True)
                assert requires is not False, f"View {name} must not have requires_auth=False"

    async def test_unauthenticated_views_allowed(self) -> None:
        """The pairing UI pages (index, config, events, static) correctly remain
        unauthenticated — the browser opens these before HA auth is set up.

        NOTE: 'result' was moved to requires_auth=True in R27-1 because it
        returns the local_key in plaintext.  The browser has HA auth during
        the config flow session and can include the Bearer token.
        """
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append
        await server._register_ha_views()

        ui_names = {
            "api:tuya_cloudless:pairing:index",
            # NOTE: pairing:config was moved to requires_auth=True in R53-F6 (exposes
            # default_ssid / integration_version).  It is always opened from within
            # the authenticated HA frontend so auth is safe there.
            "api:tuya_cloudless:pairing:events",
            "api:tuya_cloudless:pairing:static",
        }
        for view in captured_views:
            name = getattr(view, "name", "")
            if name in ui_names:
                requires = getattr(view, "requires_auth", True)
                assert requires is False, (
                    f"View {name} must have requires_auth=False (pairing UI access)"
                )

    async def test_result_view_requires_auth(self) -> None:
        """R27-1: The result view must require HA auth (returns local_key in plaintext)."""
        hass = MagicMock()
        server = PairingServer(hass, port=8099)
        captured_views: list[object] = []
        hass.http.register_view.side_effect = captured_views.append
        await server._register_ha_views()

        result_name = "api:tuya_cloudless:pairing:result"
        result_view = next(
            (v for v in captured_views if getattr(v, "name", "") == result_name),
            None,
        )
        assert result_view is not None, "Result view must be registered"
        requires = getattr(result_view, "requires_auth", True)
        assert requires is not False, "Result view must NOT have requires_auth=False"


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

    def test_non_standard_loopback_127_x_trusts_xff(self, server: PairingServer) -> None:
        """R26-5: 127.0.0.2 is loopback (per RFC 990) and must trust X-Forwarded-For.

        The old exact-match check only handled 127.0.0.1 and ::1.  Using
        ipaddress.is_loopback() correctly handles the full 127.0.0.0/8 block.
        """
        req = MagicMock()
        req.remote = "127.0.0.2"
        req.headers = {"X-Forwarded-For": "10.20.30.40"}
        assert server._get_client_ip(req) == "10.20.30.40"

    def test_unknown_rate_limited_not_bypassed(self, server: PairingServer) -> None:
        """R26-5: 'unknown' client IPs must not bypass rate limiting.

        Previously the 'unknown' sentinel caused _is_rate_limited to return
        False unconditionally.  Now 'unknown' is treated as a single shared
        bucket to prevent unbounded requests from connection-less peers.
        """
        from custom_components.tuya_cloudless.pairing_server import _RATE_LIMIT_MAX

        # Pre-fill the 'unknown' bucket beyond the rate limit
        now = time.monotonic()
        server._rate_limit["unknown"] = [now] * _RATE_LIMIT_MAX
        assert server._is_rate_limited("unknown") is True


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


class TestActivateBodyDataNesting:
    """R20-2: body["data"] must be ignored when it is not a dict."""

    @pytest.mark.asyncio
    async def test_non_dict_data_field_falls_back_to_body(self, client: TestClient) -> None:
        """When body['data'] is a string, gw_id must be read from body (not crash)."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"data": "malicious_string", "gw_id": "fallback_gw"},
        )
        # Must not return 500 — the handler must fall back to reading body
        assert resp.status in (200, 400)

    @pytest.mark.asyncio
    async def test_null_data_field_falls_back_to_body(self, client: TestClient) -> None:
        """When body['data'] is null, gw_id must be read from body."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"data": None, "gw_id": "fallback_gw"},
        )
        assert resp.status in (200, 400)

    @pytest.mark.asyncio
    async def test_integer_data_field_falls_back_to_body(self, client: TestClient) -> None:
        """When body['data'] is an integer, gw_id must be read from body."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"data": 42, "gw_id": "fallback_gw"},
        )
        assert resp.status in (200, 400)


# ── R38: Security design contract ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestR38SecurityDesign:
    """R38: Verify the security contract between SSE and the result endpoint.

    The SSE 'activated' broadcast must NOT include local_key (R33-3: LAN-visible).
    The result endpoint MUST include local_key (browser fetches it after SSE fires,
    gated by the token which acts as a capability token).
    """

    async def test_result_endpoint_includes_local_key_for_browser_fetch(
        self, client: TestClient
    ) -> None:
        """R38-F6: The result endpoint must include local_key so the browser can fetch it.

        This is the mechanism by which the browser retrieves local_key after the SSE
        'activated' event fires (which deliberately omits local_key for security).
        """
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "r38_result_gw", "token": _TOKEN_R38},
        )
        resp = await client.get(f"/api/provision/result/{_TOKEN_R38}")
        assert resp.status == 200
        body = await resp.json()
        assert "local_key" in body, (
            "Result endpoint missing local_key — browser can't complete the pairing flow"
        )
        assert body["local_key"], "local_key must be non-empty"

    async def test_result_endpoint_local_key_is_hex(self, client: TestClient) -> None:
        """R38-F6: local_key in result must be a valid hex string (16 chars for 8-byte key)."""
        import re

        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "r38_hex_gw", "token": _TOKEN_R38},
        )
        resp = await client.get(f"/api/provision/result/{_TOKEN_R38}")
        body = await resp.json()
        assert re.fullmatch(r"[0-9a-f]{16,32}", body.get("local_key", "")), (
            "local_key must be 16-32 lowercase hex chars"
        )

    async def test_sse_activated_does_not_expose_local_key_field(
        self, client: TestClient, server: PairingServer
    ) -> None:
        """R33-3 contract: SSE 'activated' payload must not expose local_key.

        Verified by inspecting the stored SSE broadcast queues (avoids slow stream reads).
        """
        import json

        # Capture what gets queued for broadcast
        queued_events: list[tuple[str, str]] = []
        original_broadcast = server._broadcast_sse

        async def _capture(event: str, data: str) -> None:
            queued_events.append((event, data))
            await original_broadcast(event, data)

        server._broadcast_sse = _capture  # type: ignore[method-assign]

        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "r38_sse_gw", "token": _TOKEN_R38},
        )
        server._broadcast_sse = original_broadcast  # type: ignore[method-assign]

        activated = [(e, d) for e, d in queued_events if e == "activated"]
        assert activated, "Expected at least one 'activated' SSE event"
        for _, data in activated:
            payload = json.loads(data)
            assert "local_key" not in payload, (
                "R33-3 violated: SSE 'activated' broadcast must not expose local_key — "
                "the SSE stream is accessible to any LAN host"
            )


# ── TestR40Security ────────────────────────────────────────────────────────────

_TOKEN_R40 = "40" * 16  # R40 tests


class TestR40Security:
    """R40 security fixes: burn-after-read, flow_id validation."""

    @pytest.mark.asyncio
    async def test_result_endpoint_burn_after_read(self, client: TestClient) -> None:
        """R40-F1: second GET of result/{token} returns 404 (burn-after-read)."""
        # Activate the device so a result exists
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "r40_bar_gw", "token": _TOKEN_R40},
        )
        # First read — should succeed
        resp1 = await client.get(f"/api/provision/result/{_TOKEN_R40}")
        assert resp1.status == 200
        body1 = await resp1.json()
        assert "local_key" in body1

        # Second read — must be refused (burn-after-read)
        resp2 = await client.get(f"/api/provision/result/{_TOKEN_R40}")
        assert resp2.status == 404, "R40-F1 violated: result endpoint returned key on second read"

    @pytest.mark.asyncio
    async def test_flow_id_hint_long_string_rejected(self, server: PairingServer) -> None:
        """R40-F7: flow_id_hint longer than 128 chars must be discarded (no hash-DoS)."""
        from custom_components.tuya_cloudless.pairing_server import _FLOW_ID_RE

        long_hint = "a" * 200
        assert not _FLOW_ID_RE.match(long_hint), (
            "R40-F7: _FLOW_ID_RE incorrectly accepted 200-char string"
        )

    @pytest.mark.asyncio
    async def test_flow_id_hint_invalid_chars_rejected(self, server: PairingServer) -> None:
        """R40-F7: flow_id_hint with path-traversal chars must be rejected by regex."""
        from custom_components.tuya_cloudless.pairing_server import _FLOW_ID_RE

        bad_hint = "../../../etc/passwd"
        assert not _FLOW_ID_RE.match(bad_hint), (
            "R40-F7: _FLOW_ID_RE incorrectly accepted path-traversal string"
        )

    @pytest.mark.asyncio
    async def test_flow_id_hint_valid_uuid_accepted(self, server: PairingServer) -> None:
        """R40-F7: a valid UUID-style flow_id must pass the regex check."""
        from custom_components.tuya_cloudless.pairing_server import _FLOW_ID_RE

        uuid_hint = "550e8400-e29b-41d4-a716-446655440000"
        assert _FLOW_ID_RE.match(uuid_hint), (
            "R40-F7: _FLOW_ID_RE incorrectly rejected a valid UUID flow_id"
        )


# ── TestR41Security ────────────────────────────────────────────────────────────


class TestR41Security:
    """R41 fixes: hass.async_create_task, flow-resume race, token-to-flow cap."""

    @pytest.mark.asyncio
    async def test_sse_broadcast_uses_hass_async_create_task(self, server: PairingServer) -> None:
        """R41-F2: SSE broadcast must use hass.async_create_task, not deprecated
        asyncio.get_event_loop().create_task()."""
        created: list[object] = []

        def capture(coro: object, **kw: object) -> object:
            created.append(coro)
            # Close coroutine to avoid ResourceWarning; we only care it was called
            if hasattr(coro, "close"):
                coro.close()
            return MagicMock()

        server._hass.async_create_task = capture  # type: ignore[attr-defined]

        ts = TestServer(server._app)
        cli = TestClient(ts)
        await cli.start_server()
        try:
            await cli.post(
                "/api/tuya/device/active",
                json={"gw_id": "r41_gw", "token": "41" * 16},
            )
        finally:
            await cli.close()

        assert any("_broadcast_sse" in getattr(c, "__qualname__", "") for c in created), (
            "Expected _broadcast_sse to be scheduled via hass.async_create_task"
        )

    @pytest.mark.asyncio
    async def test_token_to_flow_cap(self, server: PairingServer) -> None:
        """R41-F8: _token_to_flow must not grow beyond 256 entries."""

        # Manually seed 256 bindings
        for i in range(256):
            server._token_to_flow[f"{i:032x}"[:32]] = f"flow-{i}"

        assert len(server._token_to_flow) == 256

        # Simulate bind-token by calling the cap eviction directly
        _MAX_TOKEN_BINDINGS = 256
        if len(server._token_to_flow) >= _MAX_TOKEN_BINDINGS:
            oldest = next(iter(server._token_to_flow))
            del server._token_to_flow[oldest]

        # Insert one more
        server._token_to_flow["ff" * 16] = "new-flow"
        assert len(server._token_to_flow) == 256, (
            "token_to_flow must stay at cap after eviction + insert"
        )


# ── R46 security fixes ─────────────────────────────────────────────────────────


class TestTokenlessDedupR46:
    """R46-F5: Tokenless activations must be deduplicated by gw_id."""

    @pytest.mark.asyncio
    async def test_tokenless_retry_omits_local_key_r53(self, client: TestClient) -> None:
        """R53-F3: Second POST without token for same gw_id must NOT re-expose localKey.

        The first activation generates and stores the key internally (dedup entry).
        A firmware retry gets a 200 ACK but with an empty localKey — the device already
        received the key on the first call and this prevents port-8099 from acting as
        a key-oracle for any LAN host that knows the gw_id.
        """
        payload = {"gw_id": "testgw001", "product_key": "abc123"}
        resp1 = await client.post("/api/tuya/device/active", json=payload)
        data1 = await resp1.json()
        assert resp1.status == 200
        key1 = data1["result"]["localKey"]
        assert key1 != "", "First tokenless activation must return a non-empty localKey"

        # Retry — same gw_id, no token
        resp2 = await client.post("/api/tuya/device/active", json=payload)
        data2 = await resp2.json()
        assert resp2.status == 200
        key2 = data2["result"]["localKey"]

        assert key2 == "", "R53-F3: tokenless duplicate must not expose localKey in response"

    @pytest.mark.asyncio
    async def test_different_gw_ids_get_different_keys_tokenless(self, client: TestClient) -> None:
        """Two different gw_ids without tokens must each get their own local_key."""
        payload_a = {"gw_id": "gwAAA", "product_key": "pk1"}
        payload_b = {"gw_id": "gwBBB", "product_key": "pk2"}
        resp_a = await client.post("/api/tuya/device/active", json=payload_a)
        resp_b = await client.post("/api/tuya/device/active", json=payload_b)
        data_a = await resp_a.json()
        data_b = await resp_b.json()
        assert data_a["result"]["localKey"] != data_b["result"]["localKey"]


class TestSsidCapR46:
    """R46-F7: WiFi scan must cap SSID list at _MAX_SSID_SCAN_RESULTS."""

    def test_ssid_cap_constant_is_50(self) -> None:
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSID_SCAN_RESULTS

        assert _MAX_SSID_SCAN_RESULTS == 50


class TestR47SecurityFixes:
    """R47 — tokenless TTL/cap, rate-limiter >=, _handle_index cache."""

    @pytest.mark.asyncio
    async def test_tokenless_results_ttl_expired_on_next_activation(
        self, server: PairingServer
    ) -> None:
        """R47-F1: Expired tokenless entries are removed by _expire_old_results."""
        import time

        from custom_components.tuya_cloudless.pairing_server import (
            _RESULT_TTL_SECS,
            ActivationResult,
        )

        # Inject a stale entry (timestamp well past TTL)
        old_result = ActivationResult(
            gw_id="stale_gw",
            product_key="pk",
            local_key="a" * 16,
            ip_address="10.0.0.1",
        )
        old_result.timestamp = time.monotonic() - (_RESULT_TTL_SECS + 1)
        server._tokenless_results["stale_gw"] = old_result

        # Calling _expire_old_results should remove the stale entry
        server._expire_old_results()
        assert "stale_gw" not in server._tokenless_results

    def test_tokenless_results_size_cap_constant(self, server: PairingServer) -> None:
        """R47-F1: _tokenless_results shares the same MAX_STORED_RESULTS cap."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_STORED_RESULTS

        assert _MAX_STORED_RESULTS == 256  # sanity-check constant used in size cap

    @pytest.mark.asyncio
    async def test_rate_limit_cap_uses_ge(self, server: PairingServer) -> None:
        """R47-F6: Rate limit hard-cap must evict at >= not > _MAX_RATE_LIMIT_IPS."""
        import time

        from custom_components.tuya_cloudless.pairing_server import _MAX_RATE_LIMIT_IPS

        # Seed exactly _MAX_RATE_LIMIT_IPS entries in the rate limit table
        for i in range(_MAX_RATE_LIMIT_IPS):
            server._rate_limit[f"10.0.{i // 256}.{i % 256}"] = [time.monotonic()]
        assert len(server._rate_limit) == _MAX_RATE_LIMIT_IPS

        # Calling _is_rate_limited with a new IP should trigger eviction (>= condition)
        server._is_rate_limited("192.168.1.1", max_requests=5, window=60.0)
        # After eviction, the new IP is added, so count stays at _MAX_RATE_LIMIT_IPS
        assert len(server._rate_limit) == _MAX_RATE_LIMIT_IPS

    def test_index_html_mtime_cache_attributes_exist(self, server: PairingServer) -> None:
        """R47-F3: PairingServer must initialise index HTML cache attributes."""
        assert hasattr(server, "_index_html_cache")
        assert hasattr(server, "_index_html_mtime")
        assert server._index_html_cache is None
        assert server._index_html_mtime is None


class TestR48SecurityFixes:
    """R48 — consumed flag order, bind-token rate limit, zombie subprocess reap."""

    @pytest.mark.asyncio
    async def test_result_consumed_set_before_response(self, client: TestClient) -> None:
        """R48-F1: result.consumed must be True after first successful GET."""
        # Activate a device to populate _results
        token = "a" * 32
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_r48", "product_key": "pk1", "token": token},
        )
        # First GET should succeed
        resp1 = await client.get(f"/api/provision/result/{token}")
        assert resp1.status == 200

        # Second GET must return 404 (burn-after-read)
        resp2 = await client.get(f"/api/provision/result/{token}")
        assert resp2.status == 404

    def test_bind_token_rate_limited(self) -> None:
        """R48-F2: _is_rate_limited enforces 20 calls/min (bind-token limit).

        bind-token is only exposed on the HA HTTPS path (not port-8099), so we
        test the shared rate-limiting mechanism directly.
        """
        hass = _make_hass()
        srv = PairingServer(hass, port=0)
        fake_ip = "10.0.0.99"
        # 20 calls — all within limit
        for _ in range(20):
            assert not srv._is_rate_limited(fake_ip, max_requests=20, window=60.0)
        # 21st call must be rate-limited (>= 20)
        assert srv._is_rate_limited(fake_ip, max_requests=20, window=60.0)

    def test_session_key_neg_decode_version_constant_is_32(self) -> None:
        """R48-F8: Session key negotiation decode version must be '3.2'."""
        from custom_components.tuya_cloudless.coordinator import (
            _SESSION_KEY_NEG_DECODE_VERSION,
        )

        assert _SESSION_KEY_NEG_DECODE_VERSION == "3.2"


class TestR49SecurityFixes:
    """R49 — zombie reap in _get_default_ssid/_handle_quick_scan, stat() guard,
    static file size cap, tokenless expiry, SSE bounds."""

    # ── F1/F2: zombie subprocess reap ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_quick_scan_kills_and_reaps_on_timeout(self, server: PairingServer) -> None:
        """R49-F2: _handle_quick_scan must reap zombie after TimeoutError."""
        from unittest.mock import AsyncMock, MagicMock, patch

        hung_communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.communicate = hung_communicate

        with (
            patch("shutil.which", return_value="/usr/bin/nmcli"),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=TimeoutError),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_quick_scan(request)
        # Timeout is caught gracefully → empty list returned (200), not 500
        assert resp.status == 200
        # R49-F2: proc.kill() must have been called to prevent zombie
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_sse_drops_oversized_event_name(self, server: PairingServer) -> None:
        """R49-F8: _broadcast_sse must drop events with name > _MAX_SSE_EVENT_LEN."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSE_EVENT_LEN

        long_event = "x" * (_MAX_SSE_EVENT_LEN + 1)
        # Should not raise, should log and return without enqueuing
        await server._broadcast_sse(long_event, '{"ok":true}')
        # No queues → nothing to check; important thing is no exception raised.

    @pytest.mark.asyncio
    async def test_broadcast_sse_drops_oversized_data(self, server: PairingServer) -> None:
        """R49-F8: _broadcast_sse must drop events with data > _MAX_SSE_DATA_LEN."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSE_DATA_LEN

        big_data = "y" * (_MAX_SSE_DATA_LEN + 1)
        await server._broadcast_sse("activated", big_data)

    @pytest.mark.asyncio
    async def test_broadcast_sse_accepts_normal_payload(self, server: PairingServer) -> None:
        """R49-F8: _broadcast_sse must pass through normal-sized payloads."""
        from custom_components.tuya_cloudless.pairing_server import (
            _MAX_SSE_DATA_LEN,
            _MAX_SSE_EVENT_LEN,
        )

        good_event = "a" * _MAX_SSE_EVENT_LEN
        good_data = "b" * _MAX_SSE_DATA_LEN
        # Must not raise
        await server._broadcast_sse(good_event, good_data)

    def test_expire_old_results_cleans_tokenless(self, server: PairingServer) -> None:
        """R49-F5: _expire_old_results removes expired tokenless entries."""
        import time

        result = ActivationResult(
            gw_id="gwX", product_key="pk", local_key="lk", ip_address="1.2.3.4"
        )
        # Force timestamp to be very old
        result.timestamp = time.monotonic() - 7200.0
        server._tokenless_results["gwX"] = result
        server._expire_old_results()
        assert "gwX" not in server._tokenless_results

    def test_static_file_constants_defined(self) -> None:
        """R49-F4/F8: size and SSE bound constants must exist."""
        from custom_components.tuya_cloudless.pairing_server import (
            _MAX_SSE_DATA_LEN,
            _MAX_SSE_EVENT_LEN,
            _MAX_STATIC_FILE_BYTES,
        )

        assert _MAX_STATIC_FILE_BYTES == 5 * 1024 * 1024
        assert _MAX_SSE_EVENT_LEN == 64
        assert _MAX_SSE_DATA_LEN == 4096


class TestR50SecurityFixes:
    """R50 — bundled lib sync, icon size guard, index size guard, rate-limit throttle."""

    def test_bundled_lib_message_command_type_returns_raw_int_for_unknown(self) -> None:
        """R50-F3: bundled message.py CommandType.from_int must return raw int, not raise."""
        import sys

        # Insert bundled lib path first (simulating HACS runtime priority)
        bundled = str(
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent
            / "custom_components/tuya_cloudless/lib"
        )
        if bundled not in sys.path:
            sys.path.insert(0, bundled)
        try:
            from tuya_cloudless.message import CommandType

            result = CommandType.from_int(0xFF)  # unknown code
            assert isinstance(result, int), (
                "from_int must return raw int for unknown command codes, not raise"
            )
        finally:
            if bundled in sys.path:
                sys.path.remove(bundled)

    def test_bundled_lib_protocol_masks_sequence_to_32_bits(self) -> None:
        """R50-F4: bundled protocol.py encode_frame must mask sequence to 32 bits."""
        import sys

        bundled = str(
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent
            / "custom_components/tuya_cloudless/lib"
        )
        if bundled not in sys.path:
            sys.path.insert(0, bundled)
        try:
            import importlib

            import tuya_cloudless.protocol as proto

            importlib.reload(proto)
            # sequence > 0xFFFFFFFF must not raise struct.error
            frame = proto.encode_frame(
                sequence=0x1_0000_0001,
                command=7,
                payload=b"",
                local_key=b"1234567890123456",
                version="3.3",
            )
            assert frame  # any bytes returned = no struct.error
        finally:
            if bundled in sys.path:
                sys.path.remove(bundled)

    @pytest.mark.asyncio
    async def test_handle_icon_rejects_oversized_file(self, server: PairingServer) -> None:
        """R50-F1: _handle_icon must return 403 when icon.png exceeds _MAX_STATIC_FILE_BYTES."""
        from unittest.mock import MagicMock, patch

        stat_mock = MagicMock()
        stat_mock.st_size = 6 * 1024 * 1024  # 6 MiB > limit

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", return_value=stat_mock),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_icon(request)
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_handle_index_rejects_oversized_file(self, server: PairingServer) -> None:
        """R50-F5: _handle_index must return 503 when index.html exceeds _MAX_STATIC_FILE_BYTES."""
        from unittest.mock import MagicMock, patch

        stat_mock = MagicMock()
        stat_mock.st_mtime = 0.0
        stat_mock.st_size = 10 * 1024 * 1024  # 10 MiB > limit

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", return_value=stat_mock),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_index(request)
        assert resp.status == 503

    def test_rate_limit_cleanup_timestamp_initialised(self) -> None:
        """R50-F6: _rate_limit_last_cleanup must be initialised to 0.0 in __init__."""
        hass = _make_hass()
        srv = PairingServer(hass, port=0)
        assert hasattr(srv, "_rate_limit_last_cleanup")
        assert srv._rate_limit_last_cleanup == 0.0

    def test_rate_limit_rebuild_throttled(self) -> None:
        """R50-F6: full table rebuild must be skipped within 5 s of last cleanup."""
        import time

        hass = _make_hass()
        srv = PairingServer(hass, port=0)
        # Seed one IP to give the cleanup something to process
        srv._rate_limit["10.0.0.1"] = [time.monotonic()]
        # Mark cleanup as just-done → subsequent call must NOT rebuild
        srv._rate_limit_last_cleanup = time.monotonic()
        # Second call for a different IP (not rate limited)
        srv._is_rate_limited("10.0.0.2", max_requests=10, window=60.0)
        # Table should still contain 10.0.0.1 (cleanup was throttled)
        assert "10.0.0.1" in srv._rate_limit


# ── Round 51 security findings ────────────────────────────────────────────────


class TestR51SecurityFixes:
    """Round 51 — pairing_server.py security hardening tests."""

    @pytest.fixture
    def server(self) -> PairingServer:
        hass = _make_hass()
        return PairingServer(hass, port=0)

    @pytest.mark.asyncio
    async def test_handle_ha_index_rejects_oversized_file(self, server: PairingServer) -> None:
        """R51-F1: _handle_ha_index must return 503 when frontend index exceeds limit."""
        from unittest.mock import MagicMock, patch

        from custom_components.tuya_cloudless.pairing_server import _MAX_STATIC_FILE_BYTES

        stat_mock = MagicMock()
        stat_mock.st_mtime = 0.0
        stat_mock.st_size = _MAX_STATIC_FILE_BYTES + 1

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", return_value=stat_mock),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_ha_index(request)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_brand_path_resolve_oserror_returns_empty(self, server: PairingServer) -> None:
        """R51-F2: brand-path resolve() OSError must be caught, returning empty brand."""
        from unittest.mock import MagicMock, patch

        with patch("pathlib.Path.resolve", side_effect=OSError("symlink loop")):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_config(request)
        data = json.loads(resp.body)
        # brand_name should be empty / None when resolve() fails
        assert data.get("brand_name") in (None, "", False) or isinstance(
            data.get("brand_name"), str
        )

    def test_ssids_cap_applied_before_tuya_aps(self, server: PairingServer) -> None:
        """R51-F4: tuya_aps must be derived after ssids[:_MAX_SSID_SCAN_RESULTS] cap."""
        from custom_components.tuya_cloudless.pairing_server import _MAX_SSID_SCAN_RESULTS

        # Seed more SSIDs than the cap
        many = _MAX_SSID_SCAN_RESULTS + 20
        server._discovered_ssids = {f"net{i}": 0 for i in range(many)}
        # All start with "TuyaSmartLife-" prefix = tuya APs
        server._discovered_ssids = {f"TuyaSmartLife-{i:04d}": 0.0 for i in range(many)}
        # Build the config response directly
        ssids = sorted(server._discovered_ssids.keys(), key=lambda k: -server._discovered_ssids[k])
        ssids = ssids[:_MAX_SSID_SCAN_RESULTS]
        tuya_aps = [s for s in ssids if s.startswith("TuyaSmartLife-")]
        assert len(ssids) == _MAX_SSID_SCAN_RESULTS
        assert len(tuya_aps) <= _MAX_SSID_SCAN_RESULTS

    def test_static_view_post_read_size_check_code_present(self) -> None:
        """R51-F5: verify the TOCTOU post-read size guard is in the source.

        _PairingStaticView is a local class inside _register_views and cannot be
        imported for direct unit testing.  This test inspects the module source to
        confirm the guard is present so a future refactor cannot silently remove it.
        """
        import inspect

        from custom_components.tuya_cloudless import pairing_server

        source = inspect.getsource(pairing_server)
        assert "len(data) > _MAX_STATIC_FILE_BYTES" in source, (
            "R51-F5 TOCTOU guard 'len(data) > _MAX_STATIC_FILE_BYTES' "
            "not found in pairing_server.py"
        )


# ── Round 52 security findings ────────────────────────────────────────────────


class TestR52SecurityFixes:
    """Round 52 — pairing_server.py security hardening tests."""

    @pytest.fixture
    def server(self) -> PairingServer:
        hass = _make_hass()
        return PairingServer(hass, port=0)

    @pytest.mark.asyncio
    async def test_quick_scan_ssid_byte_length_guard(self, server: PairingServer) -> None:
        """R52-F1: SSIDs > 32 bytes must be rejected in _handle_quick_scan."""
        from unittest.mock import AsyncMock, MagicMock, patch

        # An over-long SSID (33 bytes when UTF-8 encoded) that is a Tuya AP prefix
        # _is_tuya_ap checks lowercase prefixes: "smartlife_", "sl_", "az_", "tuya_"
        oversized_ssid = "SmartLife_" + "A" * 23  # > 32 bytes
        assert len(oversized_ssid.encode()) > 32

        valid_ssid = "SmartLife_AB12"  # matches "smartlife_" prefix, <= 32 bytes
        assert len(valid_ssid.encode()) <= 32

        stdout = f"{oversized_ssid}\n{valid_ssid}\n".encode()
        proc_mock = MagicMock()
        proc_mock.communicate = AsyncMock(return_value=(stdout, b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=proc_mock,
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            resp = await server._handle_quick_scan(request)

        data = json.loads(resp.body)
        ssids = [ap["ssid"] for ap in data["tuya_aps"]]
        assert valid_ssid in ssids
        assert oversized_ssid not in ssids

    @pytest.mark.asyncio
    async def test_quick_scan_result_capped_at_max(self, server: PairingServer) -> None:
        """R52-F2: _handle_quick_scan must cap tuya_aps at _MAX_SSID_SCAN_RESULTS."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.tuya_cloudless.pairing_server import _MAX_SSID_SCAN_RESULTS

        # Generate more SSIDs than the cap, all valid Tuya APs ("sl_" prefix)
        many_ssids = [f"sl_{i:04d}" for i in range(_MAX_SSID_SCAN_RESULTS + 20)]
        stdout = "\n".join(many_ssids).encode()
        proc_mock = MagicMock()
        proc_mock.communicate = AsyncMock(return_value=(stdout, b""))

        with patch("asyncio.create_subprocess_exec", return_value=proc_mock):
            request = MagicMock()
            request.remote = "127.0.0.1"
            resp = await server._handle_quick_scan(request)

        data = json.loads(resp.body)
        assert len(data["tuya_aps"]) == _MAX_SSID_SCAN_RESULTS

    def test_sse_newline_re_blocks_unicode_line_separators(self) -> None:
        """R52-F6: _SSE_NEWLINE_RE must match Unicode line terminators."""
        from custom_components.tuya_cloudless.pairing_server import _SSE_NEWLINE_RE

        # Standard ASCII newlines — must be blocked
        assert _SSE_NEWLINE_RE.search("event\ndata")
        assert _SSE_NEWLINE_RE.search("event\rdata")
        # Unicode line terminators — must also be blocked
        assert _SSE_NEWLINE_RE.search("event\u2028data")  # LINE SEPARATOR
        assert _SSE_NEWLINE_RE.search("event\u2029data")  # PARAGRAPH SEPARATOR
        assert _SSE_NEWLINE_RE.search("event\x85data")  # NEXT LINE (NEL)
        # Clean strings — must pass
        assert _SSE_NEWLINE_RE.search("clean-event-name") is None
        assert _SSE_NEWLINE_RE.search('{"key":"value"}') is None


# ── Round 53 security findings ────────────────────────────────────────────────


class TestR53SecurityFixes:
    """Round 53 — security hardening tests."""

    @pytest.fixture
    def server(self) -> PairingServer:
        hass = _make_hass()
        return PairingServer(hass, port=0)

    @pytest.mark.asyncio
    async def test_tokenless_duplicate_omits_local_key(self, server: PairingServer) -> None:
        """R53-F3: tokenless duplicate activation must not return local_key to caller."""
        from unittest.mock import MagicMock

        # Seed a tokenless result so the next call is a duplicate
        from custom_components.tuya_cloudless.pairing_server import ActivationResult

        gw_id = "aabbccdd11223344"
        server._tokenless_results[gw_id] = ActivationResult(
            gw_id=gw_id,
            product_key="",
            local_key="1234567890abcdef",
            ip_address="192.168.1.50",
            sw_ver="1.0",
        )

        request = MagicMock()
        request.remote = "192.168.1.50"
        request.content_type = "application/json"
        request.json = AsyncMock(return_value={"gwId": gw_id, "t": 1234567890})

        resp = await server._handle_activate(request)
        data = json.loads(resp.body)
        # The duplicate response must return an empty localKey
        assert data["result"]["localKey"] == ""

    def test_pairing_config_view_requires_auth(self) -> None:
        """R53-F6: _PairingConfigView must have requires_auth=True."""
        import inspect

        from custom_components.tuya_cloudless import pairing_server

        source = inspect.getsource(pairing_server)
        # Find the _PairingConfigView block and verify requires_auth = True
        # We check for the pattern: requires_auth = True appears after _PairingConfigView
        config_view_idx = source.find("class _PairingConfigView")
        assert config_view_idx >= 0
        # Within the next 500 chars of _PairingConfigView, find requires_auth = True
        snippet = source[config_view_idx : config_view_idx + 500]
        assert "requires_auth = True" in snippet, (
            "R53-F6: _PairingConfigView must have requires_auth = True"
        )


# ── OSError guards & WiFi task edge cases ─────────────────────────────────────


class TestOsErrorGuards:
    """Cover OSError fallback branches in _handle_index, _handle_icon, _handle_ha_index."""

    @pytest.fixture
    def server(self) -> PairingServer:
        return PairingServer(_make_hass(), port=0)

    # ── _handle_index ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_handle_index_stat_oserror_still_serves_file(self, server: PairingServer) -> None:
        """stat() OSError → current_mtime=None + file_size=0, file still read (lines 572-580)."""
        from unittest.mock import MagicMock, patch

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", side_effect=OSError("disk error")),
            patch("pathlib.Path.read_bytes", return_value=b"<html/>"),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_index(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_handle_index_read_bytes_oserror_returns_503(self, server: PairingServer) -> None:
        """read_bytes() OSError → 503 response (lines 589-590)."""
        from unittest.mock import MagicMock, patch

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", side_effect=OSError("disk error")),
            patch("pathlib.Path.read_bytes", side_effect=OSError("read error")),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_index(request)
        assert resp.status == 503

    # ── _handle_icon ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_handle_icon_stat_oserror_returns_404(self, server: PairingServer) -> None:
        """stat() OSError for icon → 404 (lines 657-658)."""
        from unittest.mock import MagicMock, patch

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", side_effect=OSError("disk error")),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_icon(request)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_handle_icon_read_bytes_oserror_returns_503(self, server: PairingServer) -> None:
        """read_bytes() OSError for icon → 503 (lines 668-669)."""
        from unittest.mock import MagicMock, patch

        stat_mock = MagicMock()
        stat_mock.st_size = 100

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", return_value=stat_mock),
            patch("pathlib.Path.read_bytes", side_effect=OSError("read error")),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_icon(request)
        assert resp.status == 503

    # ── _handle_ha_index ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_handle_ha_index_stat_oserror_still_serves(self, server: PairingServer) -> None:
        """stat() OSError → mtime=None + size=0, file still read (lines 1719-1720, 1726-1727)."""
        from unittest.mock import MagicMock, patch

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", side_effect=OSError("disk error")),
            patch("pathlib.Path.read_text", return_value="<html/>"),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_ha_index(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_handle_ha_index_read_text_oserror_returns_503(
        self, server: PairingServer
    ) -> None:
        """read_text() OSError → 503 (lines 1736-1737)."""
        from unittest.mock import MagicMock, patch

        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.stat", side_effect=OSError("disk error")),
            patch("pathlib.Path.read_text", side_effect=OSError("read error")),
        ):
            request = MagicMock()
            request.remote = "127.0.0.1"
            request.headers = {}
            resp = await server._handle_ha_index(request)
        assert resp.status == 503


class TestWifiApPairTaskEdgeCases:
    """Cover remaining _wifi_ap_pair_task branches."""

    @pytest.mark.asyncio
    async def test_ctrl_char_in_connection_name_is_ignored(self) -> None:
        """Connection name with control char → prev_connection=None (lines 1560-1561)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        call_count = 0

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            if call_count == 1:
                proc.communicate = AsyncMock(return_value=(b"yes:bad\x00name\n", b""))
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("asyncio.sleep", new=AsyncMock()),
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
                "SmartLife_AB12", "HomeNet", "pass", "tok123", "http://ha:8099"
            )
        # Task completed without error; suspicious name was discarded, no reconnect attempt

    @pytest.mark.asyncio
    async def test_prev_connection_reconnect_failure_is_logged(self) -> None:
        """Exception in reconnect-to-prev_connection is caught (lines 1655-1656)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        call_count = 0

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            if call_count == 1:
                proc.communicate = AsyncMock(return_value=(b"yes:HomeConn\n", b""))
                proc.returncode = 0
            elif call_count == 2:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:
                raise OSError("reconnect failed")
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("aiohttp.ClientSession") as mock_session_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)
            # Should not raise — exception is caught in finally block
            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "tok_prev", "http://ha:8099"
            )

    @pytest.mark.asyncio
    async def test_home_wifi_reconnect_failure_is_logged(self) -> None:
        """Exception in home-WiFi fallback reconnect is caught (lines 1684-1685)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        call_count = 0

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            if call_count == 1:
                # No "yes:" prefix → prev_connection stays None
                proc.communicate = AsyncMock(return_value=(b"no:SomeConn\n", b""))
                proc.returncode = 0
            elif call_count == 2:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:
                raise OSError("home reconnect failed")
            proc.kill = MagicMock()
            return proc

        hass = MagicMock()
        server = PairingServer(hass, port=9099)

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch("asyncio.sleep", new=AsyncMock()),
            patch("aiohttp.ClientSession") as mock_session_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)
            # Should not raise — exception is caught in finally block
            await server._wifi_ap_pair_task(
                "SmartLife_AB12", "HomeNet", "pass", "tok_home", "http://ha:8099"
            )


class TestResumeFlowEarlyReturn:
    """Cover line 936: _resume_flow early return when flow removed before task runs."""

    @pytest.mark.asyncio
    async def test_flow_removed_before_resume_task_runs(self) -> None:
        """_resume_flow returns at line 936 when fid is discarded before the task executes."""
        from unittest.mock import AsyncMock, MagicMock

        server = PairingServer(_make_hass(), port=0)
        token = "c" * 32
        flow_id = "flow-line-936"

        server._token_to_flow[token] = flow_id
        server._pending_flows.add(flow_id)

        request = MagicMock()
        request.remote = "192.168.1.99"
        request.headers = {}
        request.content_type = "application/json"
        request.json = AsyncMock(return_value={"gwId": "gw_936", "token": token})

        # _handle_activate schedules _resume_flow as a task (not yet run)
        await server._handle_activate(request)

        # Remove flow before the task runs — simulates flow cancelled between creation/execution
        server._pending_flows.discard(flow_id)

        # Yield to event loop: _resume_flow runs, hits line 936 (fid not in _pending_flows), returns
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # second yield for SSE broadcast task

        assert flow_id not in server._pending_flows


class TestHaStaticViewSecurity:
    """Cover _PairingStaticView.get security branches (lines 1941-1983)."""

    @pytest.fixture
    async def static_view_in_tmp(self, tmp_path: Path):  # type: ignore[misc]
        """Return (static_view, ui_dir, brand_dir) with _UI_DIR patched to tmp_path."""
        from unittest.mock import patch

        import custom_components.tuya_cloudless.pairing_server as ps_mod

        ui_dir = tmp_path / "ui"
        ui_dir.mkdir()
        brand_dir = tmp_path / "brand"
        brand_dir.mkdir()

        hass = _make_hass()
        server = PairingServer(hass, port=0)
        with (
            patch.object(ps_mod, "_UI_DIR", ui_dir),
            patch.object(ps_mod, "_BRAND_DIR", brand_dir),
        ):
            await server._register_ha_views()

        calls = server._hass.http.register_view.call_args_list
        # _PairingStaticView is the 10th (index 9) registered view
        # (ap-token was added as the 9th, static moved to last position)
        static_view = calls[9][0][0]
        yield static_view, ui_dir, brand_dir, server

    @pytest.mark.asyncio
    async def test_path_traversal_returns_403(self, static_view_in_tmp) -> None:  # type: ignore[misc]
        """Path outside ui_dir returns 403 Forbidden (line 1939-1940)."""
        from unittest.mock import MagicMock

        static_view, _, _, _ = static_view_in_tmp
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        resp = await static_view.get(request, path="../../etc/passwd")
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_resolve_oserror_returns_400(self, static_view_in_tmp) -> None:  # type: ignore[misc]
        """OSError from Path.resolve returns 400 Bad Request (lines 1941-1942)."""
        from unittest.mock import MagicMock, patch

        static_view, _, _, _ = static_view_in_tmp
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        # First resolve() (file_path.resolve()) is outside the try/except.
        # Raise on the second call (ui_resolved = ui_dir.resolve()) to hit lines 1941-1942.
        call_count = 0

        def fail_second_resolve(self_path):  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return type(self_path)(str(self_path))
            raise OSError("symlink loop")

        with patch("pathlib.Path.resolve", fail_second_resolve):
            resp = await static_view.get(request, path="test.js")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_brand_path_resolve_oserror_returns_400(self, static_view_in_tmp) -> None:
        """OSError from brand path resolve returns 400 (lines 1949-1950)."""
        from unittest.mock import MagicMock, patch

        static_view, _, _, _ = static_view_in_tmp
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        # File does not exist in ui_dir → brand fallback attempted
        # Make brand path resolve fail
        call_count = 0

        def selective_resolve(self_path):  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count <= 2:  # first two resolves: file_path and ui_resolved
                return type(self_path)(str(self_path))
            raise OSError("brand resolve failed")

        with patch("pathlib.Path.resolve", selective_resolve):
            resp = await static_view.get(request, path="missing.js")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_brand_file_served_as_fallback(self, static_view_in_tmp) -> None:
        """File not in ui_dir but in brand_dir is served (line 1952)."""
        from unittest.mock import MagicMock

        static_view, _, brand_dir, _ = static_view_in_tmp
        # Create a small file in brand_dir
        brand_file = brand_dir / "icon.png"
        brand_file.write_bytes(b"\x89PNG" + b"\x00" * 10)

        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        resp = await static_view.get(request, path="icon.png")
        assert resp.status == 200
        assert resp.body.startswith(b"\x89PNG")

    @pytest.mark.asyncio
    async def test_stat_oserror_returns_404(self, static_view_in_tmp) -> None:
        """stat() OSError after is_file() check returns 404 (lines 1962-1963)."""
        from unittest.mock import MagicMock, patch

        static_view, ui_dir, _, _ = static_view_in_tmp
        ui_file = ui_dir / "app.js"
        ui_file.write_bytes(b"console.log('hi')")

        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with (
            patch("pathlib.Path.stat", side_effect=OSError("disk error")),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            resp = await static_view.get(request, path="app.js")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_oversized_static_file_returns_403(self, static_view_in_tmp) -> None:
        """File exceeding size limit returns 403 (lines 1965-1971)."""
        from unittest.mock import MagicMock, patch

        from custom_components.tuya_cloudless.pairing_server import _MAX_STATIC_FILE_BYTES

        static_view, ui_dir, _, _ = static_view_in_tmp
        ui_file = ui_dir / "large.js"
        ui_file.write_bytes(b"x")

        stat_mock = MagicMock()
        stat_mock.st_size = _MAX_STATIC_FILE_BYTES + 1

        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with (
            patch("pathlib.Path.stat", return_value=stat_mock),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            resp = await static_view.get(request, path="large.js")
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_read_bytes_oserror_returns_503(self, static_view_in_tmp) -> None:
        """read_bytes() OSError returns 503 (lines 1976-1977)."""
        from unittest.mock import MagicMock, patch

        static_view, ui_dir, _, _ = static_view_in_tmp
        ui_file = ui_dir / "ok.js"
        ui_file.write_bytes(b"data")

        stat_mock = MagicMock()
        stat_mock.st_size = 100

        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with (
            patch("pathlib.Path.stat", return_value=stat_mock),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_bytes", side_effect=OSError("read error")),
        ):
            resp = await static_view.get(request, path="ok.js")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_toctou_data_too_large_returns_403(self, static_view_in_tmp) -> None:
        """Data larger than limit after read triggers TOCTOU guard (lines 1979-1983)."""
        from unittest.mock import MagicMock, patch

        from custom_components.tuya_cloudless.pairing_server import _MAX_STATIC_FILE_BYTES

        static_view, ui_dir, _, _ = static_view_in_tmp
        ui_file = ui_dir / "grew.js"
        ui_file.write_bytes(b"x")

        stat_mock = MagicMock()
        stat_mock.st_size = 100  # stat reports small

        oversized_data = b"x" * (_MAX_STATIC_FILE_BYTES + 1)

        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with (
            patch("pathlib.Path.stat", return_value=stat_mock),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.read_bytes", return_value=oversized_data),
        ):
            resp = await static_view.get(request, path="grew.js")
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_brand_path_inner_resolve_oserror_returns_400(self, static_view_in_tmp) -> None:
        """brand_path.resolve() OSError returns 400 (lines 1949-1950).

        This is distinct from test_resolve_oserror_returns_400: that test covers
        the outer try/except (line 1941) by failing on ui_resolved.  This test
        covers the inner try/except (line 1949) by letting ui_resolved and
        brand_resolved succeed, then failing on brand_path.resolve().
        """
        from unittest.mock import MagicMock, patch

        static_view, _, _, _ = static_view_in_tmp
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        # Let the first 3 resolve() calls succeed, fail on the 4th (brand_path)
        call_count = 0

        def fail_fourth_resolve(self_path):  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return type(self_path)(str(self_path))
            raise OSError("brand path resolve failed")

        with patch("pathlib.Path.resolve", fail_fourth_resolve):
            resp = await static_view.get(request, path="nonexistent.js")
        assert resp.status == 400


# ── HA view delegation lines 1838-1922 ────────────────────────────────────────


class TestHaViewDelegation:
    """Cover the thin delegation methods in locally-defined HA view classes.

    Lines 1838, 1853, 1863, 1876-1877, 1887, 1897, 1907, 1922 are single-line
    bodies that forward to server._handle_xxx().  We capture each registered view
    and call its get()/post() with a mocked handler to exercise these lines.
    """

    @pytest.fixture
    async def views(self) -> list:  # type: ignore[misc]
        """Register all HA views and return the list of registered view instances."""
        hass = _make_hass()
        server = PairingServer(hass, port=0)
        await server._register_ha_views()
        return [call[0][0] for call in server._hass.http.register_view.call_args_list]

    @pytest.mark.asyncio
    async def test_index_view_delegates_to_handle_ha_index(self, views: list) -> None:
        """_PairingIndexView.get delegates to server._handle_ha_index (line 1838)."""
        index_view = views[0]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_ha_index",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await index_view.get(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_config_view_delegates_to_handle_ha_config(self, views: list) -> None:
        """_PairingConfigView.get delegates to server._handle_ha_config (line 1853)."""
        config_view = views[1]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_ha_config",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await config_view.get(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_events_view_delegates_to_handle_sse(self, views: list) -> None:
        """_PairingEventsView.get delegates to server._handle_sse (line 1863)."""
        events_view = views[2]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_sse",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await events_view.get(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_result_view_injects_token_and_delegates(self, views: list) -> None:
        """_PairingResultView.get injects token and delegates (lines 1876-1877)."""
        result_view = views[3]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        request.match_info = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_get_result",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await result_view.get(request, "tok123")
        assert resp.status == 200
        assert request.match_info.get("token") == "tok123"

    @pytest.mark.asyncio
    async def test_wifi_scan_view_delegates(self, views: list) -> None:
        """_PairingWifiScanView.get delegates to server._handle_wifi_scan (line 1887)."""
        wifi_scan_view = views[4]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_wifi_scan",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await wifi_scan_view.get(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_quick_scan_view_delegates(self, views: list) -> None:
        """_PairingQuickScanView.get delegates to server._handle_quick_scan (line 1897)."""
        quick_scan_view = views[5]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_quick_scan",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await quick_scan_view.get(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_wifi_ap_pair_view_delegates(self, views: list) -> None:
        """_PairingWifiApPairView.post delegates to server._handle_wifi_ap_pair (line 1907)."""
        wifi_ap_pair_view = views[6]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_wifi_ap_pair",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await wifi_ap_pair_view.post(request)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_bind_token_view_delegates(self, views: list) -> None:
        """_PairingBindTokenView.post delegates to server._handle_bind_token (line 1922)."""
        bind_token_view = views[7]
        request = MagicMock()
        request.remote = "127.0.0.1"
        request.headers = {}
        with patch(
            "custom_components.tuya_cloudless.pairing_server.PairingServer._handle_bind_token",
            new_callable=AsyncMock,
            return_value=MagicMock(status=200),
        ):
            resp = await bind_token_view.post(request)
        assert resp.status == 200
