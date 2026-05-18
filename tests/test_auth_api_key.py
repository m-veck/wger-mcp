"""ApiKeyAuthMiddleware: Bearer + X-API-Key, rejection, bypass /health."""

from __future__ import annotations

from .conftest import make_client

KEYS = "super-secret-1,super-secret-2"


def _client(**overrides: str):
    return make_client(MCP_AUTH="api_key", MCP_API_KEYS=KEYS, **overrides)


def test_health_bypasses_auth() -> None:
    with _client() as c:
        r = c.get("/health")
        assert r.status_code == 200


def test_missing_key_rejected() -> None:
    with _client() as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401
        assert "missing api key" in r.json()["reason"]


def test_bearer_key_accepted() -> None:
    with _client() as c:
        r = c.post(
            "/mcp/",
            headers={
                "Authorization": "Bearer super-secret-1",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
        assert r.status_code == 200, r.text


def test_x_api_key_header_accepted() -> None:
    with _client() as c:
        r = c.post(
            "/mcp/",
            headers={
                "X-API-Key": "super-secret-2",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
        assert r.status_code == 200, r.text


def test_wrong_key_rejected() -> None:
    with _client() as c:
        r = c.post(
            "/mcp/",
            headers={"Authorization": "Bearer wrong"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert r.status_code == 401
        assert "invalid api key" in r.json()["reason"]


def test_custom_header_name() -> None:
    with _client(MCP_API_KEY_HEADER="X-Wger-Key") as c:
        r = c.post(
            "/mcp/",
            headers={
                "X-Wger-Key": "super-secret-1",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
        assert r.status_code == 200, r.text


def test_missing_keys_raises_at_build() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="MCP_API_KEYS"):
        make_client(MCP_AUTH="api_key", MCP_API_KEYS="")
