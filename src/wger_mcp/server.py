"""FastMCP server: wger tools exposed over streamable HTTP with pluggable auth.

Tool implementations live in ``wger_mcp.tools``; this module only wires the
FastMCP instance, the upstream HTTP client, the Starlette app, and lifespan.
"""

from __future__ import annotations

import contextlib
import logging

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .auth import build_auth_middleware
from .config import Settings, load_settings
from .tools import register_all
from .wger_client import WgerClient

log = logging.getLogger("wger_mcp")


def build_app(settings: Settings) -> Starlette:
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(settings.allowed_hosts),
        allowed_hosts=settings.allowed_hosts,
    )
    mcp = FastMCP(
        "wger",
        json_response=True,
        streamable_http_path="/",
        transport_security=transport_security,
    )

    client = WgerClient(
        settings.wger_api_root,
        settings.wger_api_token,
        ca_bundle=settings.wger_ca_bundle,
    )
    register_all(mcp, client)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await client.aclose()

    async def healthcheck(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    routes = [
        Route("/health", healthcheck),
        Mount(settings.mcp_path, app=mcp.streamable_http_app()),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    # Keep `/mcp` and `/mcp/` both as MCP entry points instead of issuing a 307
    # from one to the other — MCP clients (and curl) do not follow redirects on POST.
    app.router.redirect_slashes = False
    auth_cls, auth_kwargs = build_auth_middleware(settings)
    app.add_middleware(auth_cls, **auth_kwargs)
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    log.info("MCP_AUTH=%s, MCP_PATH=%s", settings.mcp_auth.value, settings.mcp_path)
    app = build_app(settings)
    # forwarded_allow_ips="*" so uvicorn trusts X-Forwarded-Proto / -For from any
    # peer. Required when running behind a reverse proxy on a separate IP (the
    # default whitelist of 127.0.0.1 silently ignores headers from nginx etc).
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
