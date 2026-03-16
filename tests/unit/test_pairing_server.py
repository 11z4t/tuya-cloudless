"""Unit tests for custom_components/tuya_cloudless/pairing_server.

Tests the fake-cloud activation endpoint, result storage, SSE broadcasting,
and helper utilities — without needing a real HA instance or Tuya device.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

# Add lib/ and custom_components parent to import path for test environment
_REPO = Path(__file__).parent.parent.parent
for _p in [str(_REPO / "lib"), str(_REPO)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from custom_components.tuya_cloudless.pairing_server import (  # noqa: E402
    ActivationResult,
    PairingServer,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_hass() -> MagicMock:
    """Return a minimal HomeAssistant mock."""
    hass = MagicMock()
    hass.config.internal_url = "http://homeassistant.local:8123"
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
        hass = MagicMock()
        hass.config.internal_url = None
        server = PairingServer(hass, port=8099)
        assert "homeassistant.local:8099" in server.ha_local_url()

    def test_custom_port(self) -> None:
        server = PairingServer(_make_hass(), port=1234)
        assert "1234" in server.ha_local_url()


# ── /api/tuya/device/active ───────────────────────────────────────────────────


class TestActivateEndpoint:
    async def test_returns_200(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "product_key": "pk1", "token": "tok1"},
        )
        assert resp.status == 200

    async def test_response_success_flag(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "product_key": "pk1", "token": "tok1"},
        )
        body = await resp.json()
        assert body["success"] is True

    async def test_response_has_local_key(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw001", "product_key": "pk1", "token": "tok1"},
        )
        body = await resp.json()
        assert "localKey" in body["result"]
        assert len(body["result"]["localKey"]) == 32  # 16 bytes hex

    async def test_response_has_gw_id(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "device123"},
        )
        body = await resp.json()
        assert body["result"]["gwId"] == "device123"

    async def test_response_has_timestamp(self, client: TestClient) -> None:
        resp = await client.post("/api/tuya/device/active", json={})
        body = await resp.json()
        assert isinstance(body["t"], int)

    async def test_result_stored_by_token(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "stored_gw", "token": "test_token"},
        )
        # Verify via the result endpoint
        resp = await client.get("/api/provision/result/test_token")
        assert resp.status == 200

    async def test_empty_body_accepted(self, client: TestClient) -> None:
        resp = await client.post("/api/tuya/device/active", json={})
        assert resp.status == 200

    async def test_each_activation_generates_unique_key(self, client: TestClient) -> None:
        resp1 = await client.post("/api/tuya/device/active", json={"gw_id": "gw1", "token": "t1"})
        resp2 = await client.post("/api/tuya/device/active", json={"gw_id": "gw2", "token": "t2"})
        body1 = await resp1.json()
        body2 = await resp2.json()
        assert body1["result"]["localKey"] != body2["result"]["localKey"]

    async def test_nested_data_field_accepted(self, client: TestClient) -> None:
        """Some firmware wraps everything in a 'data' key."""
        resp = await client.post(
            "/api/tuya/device/active",
            json={"data": {"gw_id": "nested_gw", "token": "nested_tok"}},
        )
        body = await resp.json()
        assert body["result"]["gwId"] == "nested_gw"


# ── /api/provision/result/{token} ────────────────────────────────────────────


class TestResultEndpoint:
    async def test_pending_returns_202(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/result/unknown_token")
        assert resp.status == 202

    async def test_pending_body(self, client: TestClient) -> None:
        resp = await client.get("/api/provision/result/unknown_token")
        body = await resp.json()
        assert body["status"] == "pending"

    async def test_ok_after_activation(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_ok", "token": "tok_ok"},
        )
        resp = await client.get("/api/provision/result/tok_ok")
        assert resp.status == 200

    async def test_ok_body_has_fields(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw_fields", "product_key": "pk", "token": "tok_fields"},
        )
        resp = await client.get("/api/provision/result/tok_fields")
        body = await resp.json()
        assert body["status"] == "ok"
        assert body["gw_id"] == "gw_fields"
        assert "local_key" in body
        assert len(body["local_key"]) == 32

    async def test_ip_address_in_result(self, client: TestClient) -> None:
        await client.post(
            "/api/tuya/device/active",
            json={"gw_id": "gw", "token": "tok_ip"},
        )
        resp = await client.get("/api/provision/result/tok_ip")
        body = await resp.json()
        assert "ip_address" in body


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
        payload = {"gw_id": "dev1", "token": "tok1", "product_key": "pk"}
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
    async def test_api_json_endpoint_also_rejects_form(self, client: TestClient) -> None:
        """The /api.json alias endpoint has the same CSRF protection."""
        resp = await client.post(
            "/api.json",
            data="gw_id=evil",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status == 415
