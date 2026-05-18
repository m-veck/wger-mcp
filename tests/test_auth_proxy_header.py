"""ProxyHeaderAuthMiddleware: identity header + trusted-IP gating."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from .conftest import make_client


def _client(**overrides: str) -> TestClient:
    base = {
        "MCP_AUTH": "proxy_header",
        "MCP_PROXY_USER_HEADER": "X-Remote-User",
        "MCP_PROXY_TRUSTED_IPS": "127.0.0.1,10.0.0.0/8",
        "MCP_PROXY_ALLOWED_USERS": "alice",
    }
    base.update(overrides)
    return make_client(**base)


def _mcp_init_payload() -> dict:
    return {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }


def test_health_bypasses_auth() -> None:
    with _client() as c:
        assert c.get("/health").status_code == 200


def test_trusted_peer_with_user_passes() -> None:
    """TestClient peer IP is 127.0.0.1 which is in trusted_ips."""
    with _client() as c:
        r = c.post(
            "/mcp/",
            headers={
                "X-Remote-User": "alice",
                "Accept": "application/json, text/event-stream",
            },
            json=_mcp_init_payload(),
        )
        assert r.status_code == 200, r.text


def test_trusted_peer_missing_user_rejected() -> None:
    with _client() as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401
        assert "missing" in r.json()["reason"]


def test_user_not_in_allowlist_rejected() -> None:
    with _client() as c:
        r = c.post(
            "/mcp/",
            headers={"X-Remote-User": "intruder"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert r.status_code == 401
        assert "not allowed" in r.json()["reason"].lower()


def test_custom_user_header() -> None:
    with _client(MCP_PROXY_USER_HEADER="X-Forwarded-Preferred-Username") as c:
        r = c.post(
            "/mcp/",
            headers={
                "X-Forwarded-Preferred-Username": "alice",
                "Accept": "application/json, text/event-stream",
            },
            json=_mcp_init_payload(),
        )
        assert r.status_code == 200, r.text


def test_missing_trusted_ips_raises() -> None:
    with pytest.raises(RuntimeError, match="MCP_PROXY_TRUSTED_IPS"):
        make_client(MCP_AUTH="proxy_header", MCP_PROXY_TRUSTED_IPS="")


def test_cidr_form_accepted() -> None:
    with _client(MCP_PROXY_TRUSTED_IPS="0.0.0.0/0", MCP_PROXY_ALLOWED_USERS="") as c:
        r = c.post(
            "/mcp/",
            headers={
                "X-Remote-User": "anyone",
                "Accept": "application/json, text/event-stream",
            },
            json=_mcp_init_payload(),
        )
        assert r.status_code == 200, r.text
