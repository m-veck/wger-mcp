"""JwtAuthMiddleware: signature, iss, aud, exp, username claim, allowlist."""

from __future__ import annotations

import respx
from joserfc.jwk import RSAKey

from .conftest import AUDIENCE, ISSUER, JWKS_URI, make_client, make_token


def _client(**overrides: str):
    base = {
        "MCP_AUTH": "jwt",
        "MCP_JWT_JWKS_URI": JWKS_URI,
        "MCP_JWT_ISSUER": ISSUER,
        "MCP_JWT_AUDIENCE": AUDIENCE,
        "MCP_JWT_USERNAME_CLAIM": "preferred_username",
        "MCP_JWT_ALLOWED_USERS": "alice",
    }
    base.update(overrides)
    return make_client(**base)


def test_missing_bearer_returns_401(mock_jwks: respx.MockRouter) -> None:
    with _client() as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


def test_valid_token_passes(mock_jwks: respx.MockRouter, rsa_key: RSAKey) -> None:
    token = make_token(rsa_key)
    with _client() as c:
        r = c.post(
            "/mcp/",
            headers={
                "Authorization": f"Bearer {token}",
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


def test_wrong_audience(mock_jwks: respx.MockRouter, rsa_key: RSAKey) -> None:
    token = make_token(rsa_key, aud="someone-else")
    with _client() as c:
        r = c.post("/mcp/", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


def test_wrong_issuer(mock_jwks: respx.MockRouter, rsa_key: RSAKey) -> None:
    token = make_token(rsa_key, iss=ISSUER + "-evil")
    with _client() as c:
        r = c.post("/mcp/", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


def test_expired(mock_jwks: respx.MockRouter, rsa_key: RSAKey) -> None:
    token = make_token(rsa_key, exp_offset=-10)
    with _client() as c:
        r = c.post("/mcp/", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


def test_user_not_allowed(mock_jwks: respx.MockRouter, rsa_key: RSAKey) -> None:
    token = make_token(rsa_key, preferred_username="intruder")
    with _client() as c:
        r = c.post("/mcp/", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401
        assert "not allowed" in r.json()["reason"].lower()


def test_other_signer_rejected(mock_jwks: respx.MockRouter) -> None:
    other = RSAKey.generate_key(2048, parameters={"kid": "evil", "use": "sig"})
    token = make_token(other)
    with _client() as c:
        r = c.post("/mcp/", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


def test_custom_username_claim(mock_jwks: respx.MockRouter, rsa_key: RSAKey) -> None:
    token = make_token(rsa_key, extra={"email": "alice@example.com"})
    with _client(MCP_JWT_USERNAME_CLAIM="email",
                 MCP_JWT_ALLOWED_USERS="alice@example.com") as c:
        r = c.post(
            "/mcp/",
            headers={
                "Authorization": f"Bearer {token}",
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


def test_audience_optional(mock_jwks: respx.MockRouter, rsa_key: RSAKey) -> None:
    """When audience is empty, the aud claim is not validated."""
    import os

    os.environ.pop("MCP_JWT_AUDIENCE", None)
    token = make_token(rsa_key, aud="anything-goes")
    with _client(MCP_JWT_AUDIENCE="") as c:
        r = c.post(
            "/mcp/",
            headers={
                "Authorization": f"Bearer {token}",
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
