"""Common helpers for auth middlewares."""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

_BYPASS_EXACT = {"/health"}


def is_bypass_path(path: str) -> bool:
    return path in _BYPASS_EXACT or path.startswith("/health/")


async def reply_unauthorized(
    scope: Scope, receive: Receive, send: Send, *, reason: str, www_authenticate: str
) -> None:
    resp = JSONResponse(
        {"error": "unauthorized", "reason": reason},
        status_code=401,
        headers={"www-authenticate": www_authenticate},
    )
    await resp(scope, receive, send)


def set_identity(scope: Scope, *, strategy: str, user: str | None, **extra: object) -> None:
    state = scope.setdefault("state", {})
    state["mcp_auth"] = strategy
    state["mcp_user"] = user
    for k, v in extra.items():
        state[f"mcp_{k}"] = v


class NoAuthMiddleware:
    """No-op middleware. Use only for local dev."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        log.warning("MCP_AUTH=none — incoming requests are NOT authenticated")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            set_identity(scope, strategy="none", user=None)
        await self.app(scope, receive, send)
