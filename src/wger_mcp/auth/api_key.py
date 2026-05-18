"""Static API key strategy.

Accepts the key in either:

- ``Authorization: Bearer <key>``
- ``<header_name>: <key>`` (configurable; default ``X-API-Key``)

Keys are compared in constant time.
"""

from __future__ import annotations

import hmac
import logging

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from .base import is_bypass_path, reply_unauthorized, set_identity

log = logging.getLogger(__name__)


class ApiKeyAuthMiddleware:
    def __init__(self, app: ASGIApp, *, keys: set[str], header_name: str) -> None:
        self.app = app
        self._keys = tuple(keys)
        self._header_name = header_name.lower()
        if not self._keys:
            raise ValueError("ApiKeyAuthMiddleware requires at least one key")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if is_bypass_path(path):
            await self.app(scope, receive, send)
            return

        presented = self._extract_key(Request(scope, receive=receive))
        if presented is None:
            await reply_unauthorized(
                scope,
                receive,
                send,
                reason="missing api key",
                www_authenticate=f'Bearer realm="wger-mcp", {self._header_name}',
            )
            return

        if not self._matches(presented):
            log.warning("api key rejected (truncated): %s***", presented[:6])
            await reply_unauthorized(
                scope,
                receive,
                send,
                reason="invalid api key",
                www_authenticate=f'Bearer realm="wger-mcp", {self._header_name}',
            )
            return

        set_identity(scope, strategy="api_key", user=None)
        await self.app(scope, receive, send)

    def _extract_key(self, request: Request) -> str | None:
        header = request.headers.get(self._header_name)
        if header:
            return header.strip()
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip() or None
        return None

    def _matches(self, presented: str) -> bool:
        return any(hmac.compare_digest(presented, k) for k in self._keys)
