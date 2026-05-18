"""Pluggable auth strategies for incoming MCP requests.

The `build_auth_middleware` factory inspects settings and returns the appropriate
Starlette-style middleware class + kwargs. The middleware always:

- bypasses ``/health`` and other no-auth paths
- on success, stores identity in ``scope['state']['mcp_user']`` (str | None) and
  ``scope['state']['mcp_auth']`` (str, the strategy name)
- on failure, returns ``401`` with ``WWW-Authenticate``
"""

from __future__ import annotations

from typing import Any

from ..config import AuthStrategy, Settings
from .api_key import ApiKeyAuthMiddleware
from .base import NoAuthMiddleware
from .jwt import JwtAuthMiddleware
from .proxy_header import ProxyHeaderAuthMiddleware

__all__ = [
    "ApiKeyAuthMiddleware",
    "JwtAuthMiddleware",
    "NoAuthMiddleware",
    "ProxyHeaderAuthMiddleware",
    "build_auth_middleware",
]


def build_auth_middleware(settings: Settings) -> tuple[type, dict[str, Any]]:
    """Pick an auth middleware class + kwargs based on settings."""
    s = settings
    match s.mcp_auth:
        case AuthStrategy.none:
            return NoAuthMiddleware, {}
        case AuthStrategy.api_key:
            if not s.mcp_api_keys:
                raise RuntimeError(
                    "MCP_AUTH=api_key requires at least one key in MCP_API_KEYS"
                )
            return ApiKeyAuthMiddleware, {
                "keys": set(s.mcp_api_keys),
                "header_name": s.mcp_api_key_header,
            }
        case AuthStrategy.jwt:
            if not s.mcp_jwt_jwks_uri or not s.mcp_jwt_issuer:
                raise RuntimeError(
                    "MCP_AUTH=jwt requires MCP_JWT_JWKS_URI and MCP_JWT_ISSUER"
                )
            return JwtAuthMiddleware, {
                "jwks_uri": str(s.mcp_jwt_jwks_uri),
                "issuer": s.mcp_jwt_issuer,
                "audience": s.mcp_jwt_audience,
                "algorithms": s.mcp_jwt_algorithms,
                "username_claim": s.mcp_jwt_username_claim,
                "allowed_users": set(s.mcp_jwt_allowed_users),
                "jwks_ttl_seconds": s.mcp_jwt_jwks_ttl_seconds,
            }
        case AuthStrategy.proxy_header:
            if not s.mcp_proxy_trusted_ips:
                raise RuntimeError(
                    "MCP_AUTH=proxy_header requires MCP_PROXY_TRUSTED_IPS for safety"
                )
            return ProxyHeaderAuthMiddleware, {
                "user_header": s.mcp_proxy_user_header,
                "email_header": s.mcp_proxy_email_header,
                "trusted_ips": set(s.mcp_proxy_trusted_ips),
                "allowed_users": set(s.mcp_proxy_allowed_users),
            }
