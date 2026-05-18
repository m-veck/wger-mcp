"""Shared pytest fixtures."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.testclient import TestClient

ISSUER = "https://idp.test/realms/test"
AUDIENCE = "wger-mcp-test"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"


@pytest.fixture(autouse=True)
def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common upstream config. Each test then sets MCP_AUTH and friends."""
    monkeypatch.setenv("WGER_BASE_URL", "https://wger.test")
    monkeypatch.setenv("WGER_API_TOKEN", "wger-token")
    monkeypatch.delenv("MCP_AUTH", raising=False)
    for var in (
        "MCP_API_KEYS",
        "MCP_API_KEY_HEADER",
        "MCP_JWT_JWKS_URI",
        "MCP_JWT_ISSUER",
        "MCP_JWT_AUDIENCE",
        "MCP_JWT_ALGORITHMS",
        "MCP_JWT_USERNAME_CLAIM",
        "MCP_JWT_ALLOWED_USERS",
        "MCP_PROXY_USER_HEADER",
        "MCP_PROXY_EMAIL_HEADER",
        "MCP_PROXY_TRUSTED_IPS",
        "MCP_PROXY_ALLOWED_USERS",
        "ALLOWED_HOSTS",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def rsa_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "test-1", "use": "sig"})


@pytest.fixture
def jwks_dict(rsa_key: RSAKey) -> dict[str, Any]:
    pub = rsa_key.as_dict(private=False)
    pub.setdefault("alg", "RS256")
    return {"keys": [pub]}


def make_token(
    key: RSAKey,
    *,
    sub: str = "uuid-alice",
    preferred_username: str = "alice",
    aud: str | list[str] = AUDIENCE,
    iss: str = ISSUER,
    exp_offset: int = 300,
    extra: dict | None = None,
) -> str:
    now = int(time.time())
    claims = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "exp": now + exp_offset,
        "preferred_username": preferred_username,
    }
    if extra:
        claims.update(extra)
    header = {"alg": "RS256", "kid": key.kid, "typ": "JWT"}
    return jwt.encode(header, claims, key)


@pytest.fixture
def mock_jwks(jwks_dict: dict[str, Any]) -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(JWKS_URI).respond(json=jwks_dict)
        yield router


class _ClientIPOverride:
    """ASGI shim that rewrites ``scope['client']`` so peer-IP checks see a real address.

    Starlette's TestClient uses ``("testclient", 50000)`` by default, which doesn't
    match real IP/CIDR rules. Tests can pass ``peer_ip=`` to inject a believable peer.
    """

    def __init__(self, app, peer_ip: str) -> None:
        self.app = app
        self.peer_ip = peer_ip

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            scope = {**scope, "client": (self.peer_ip, 12345)}
        await self.app(scope, receive, send)


def make_client(*, peer_ip: str = "127.0.0.1", **overrides: str) -> TestClient:
    """Build a TestClient with the given env overrides applied to the current process."""
    import os

    for k, v in overrides.items():
        os.environ[k] = v

    from wger_mcp.config import load_settings
    from wger_mcp.server import build_app

    app = build_app(load_settings())
    return TestClient(_ClientIPOverride(app, peer_ip), base_url="http://localhost")
