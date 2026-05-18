"""Trusted-reverse-proxy header strategy.

Mirrors wger's own ``AUTH_PROXY_HEADER`` model: identity is read from a header
set by a reverse proxy (nginx, Caddy, Apache, Traefik) that has already
authenticated the user (Authelia, Authentik, Keycloak via oauth2-proxy, ...).

For safety, the request's ``client`` IP must be in ``trusted_ips``; otherwise
any HTTP client could forge the identity header. ``X-Forwarded-For`` is **not**
trusted by default — configure your proxy chain such that the immediate peer
seen by this server is the trusted proxy.
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from .base import is_bypass_path, reply_unauthorized, set_identity

log = logging.getLogger(__name__)


class ProxyHeaderAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        user_header: str,
        email_header: str | None,
        trusted_ips: set[str],
        allowed_users: set[str],
    ) -> None:
        self.app = app
        self._user_header = user_header.lower()
        self._email_header = email_header.lower() if email_header else None
        self._trusted_nets = [ipaddress.ip_network(ip, strict=False) for ip in trusted_ips]
        self._allowed = allowed_users
        if not self._trusted_nets:
            raise ValueError(
                "ProxyHeaderAuthMiddleware requires at least one trusted IP/CIDR"
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if is_bypass_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        client = scope.get("client") or ("", 0)
        peer_ip = client[0]
        if not self._is_trusted(peer_ip):
            log.warning("rejecting proxy auth from untrusted peer %s", peer_ip)
            await reply_unauthorized(
                scope, receive, send,
                reason="peer not in trusted_ips",
                www_authenticate='Proxy realm="wger-mcp"',
            )
            return

        request = Request(scope, receive=receive)
        user = request.headers.get(self._user_header)
        if not user:
            await reply_unauthorized(
                scope, receive, send,
                reason=f"missing {self._user_header} header",
                www_authenticate='Proxy realm="wger-mcp"',
            )
            return

        if self._allowed and user not in self._allowed:
            await reply_unauthorized(
                scope, receive, send,
                reason="user not allowed",
                www_authenticate='Proxy realm="wger-mcp"',
            )
            return

        email = request.headers.get(self._email_header) if self._email_header else None
        set_identity(scope, strategy="proxy_header", user=user, email=email)
        await self.app(scope, receive, send)

    def _is_trusted(self, ip_str: str) -> bool:
        if not ip_str:
            return False
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return any(ip in net for net in self._trusted_nets)
