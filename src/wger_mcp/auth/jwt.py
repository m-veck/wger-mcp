"""Generic OIDC/OAuth2 Bearer-JWT strategy.

Validates a token from any IdP that publishes a JWKS endpoint
(Keycloak, Authentik, Authelia w/ OIDC, Auth0, Okta, dex, Cognito, ...).

Configurable: JWKS URI, issuer, optional audience, allowed algorithms,
the claim used as ``username`` (default ``sub``), and an optional allowlist.
"""

from __future__ import annotations

import logging
import time

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from .base import is_bypass_path, reply_unauthorized, set_identity

log = logging.getLogger(__name__)


class _JwksCache:
    def __init__(self, uri: str, ttl_seconds: int) -> None:
        self._uri = uri
        self._ttl = ttl_seconds
        self._keys: KeySet | None = None
        self._fetched_at: float = 0.0

    async def get(self, *, force: bool = False) -> KeySet:
        now = time.time()
        if force or self._keys is None or now - self._fetched_at > self._ttl:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._uri)
                resp.raise_for_status()
                self._keys = KeySet.import_key_set(resp.json())
                self._fetched_at = now
        return self._keys


class JwtAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        jwks_uri: str,
        issuer: str,
        audience: str | None,
        algorithms: list[str],
        username_claim: str,
        allowed_users: set[str],
        jwks_ttl_seconds: int = 3600,
    ) -> None:
        self.app = app
        self._jwks = _JwksCache(jwks_uri, jwks_ttl_seconds)
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._algorithms = algorithms or ["RS256"]
        self._username_claim = username_claim
        self._allowed = allowed_users

        rules: dict = {
            "iss": {"essential": True, "value": self._issuer},
            "exp": {"essential": True},
        }
        if self._audience:
            rules["aud"] = {"essential": True, "values": [self._audience]}
        self._claims_registry = jwt.JWTClaimsRegistry(**rules)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if is_bypass_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            await reply_unauthorized(
                scope, receive, send,
                reason="missing bearer token",
                www_authenticate='Bearer realm="wger-mcp"',
            )
            return

        token = auth_header.split(" ", 1)[1].strip()
        try:
            claims = await self._verify(token)
        except JoseError as exc:
            log.warning("jwt rejected: %s", exc)
            await reply_unauthorized(
                scope, receive, send,
                reason=f"invalid token: {exc}",
                www_authenticate='Bearer realm="wger-mcp"',
            )
            return

        user = claims.get(self._username_claim)
        if self._allowed and user not in self._allowed:
            log.warning("user %r not in allowed list", user)
            await reply_unauthorized(
                scope, receive, send,
                reason="user not allowed",
                www_authenticate='Bearer realm="wger-mcp"',
            )
            return

        set_identity(scope, strategy="jwt", user=user, claims=dict(claims))
        await self.app(scope, receive, send)

    async def _verify(self, token: str) -> dict:
        keys = await self._jwks.get()
        try:
            decoded = jwt.decode(token, keys, algorithms=self._algorithms)
        except JoseError:
            keys = await self._jwks.get(force=True)
            decoded = jwt.decode(token, keys, algorithms=self._algorithms)
        self._claims_registry.validate(decoded.claims)
        return decoded.claims
