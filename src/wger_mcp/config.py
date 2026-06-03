"""Settings for wger-mcp.

Auth is provider-agnostic. Pick a strategy via `MCP_AUTH`:

- ``api_key``      static shared secret (Bearer or X-API-Key)
- ``jwt``          generic OIDC JWT validated against a JWKS endpoint
- ``proxy_header`` trust an upstream reverse-proxy identity header
                   (mirrors wger's own ``AUTH_PROXY_HEADER`` model)
- ``none``         no auth — local dev only
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthStrategy(StrEnum):
    api_key = "api_key"
    jwt = "jwt"
    proxy_header = "proxy_header"
    none = "none"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
    )

    # ---------- upstream wger ----------
    wger_base_url: HttpUrl
    wger_api_token: str
    # Path to a CA bundle for verifying the upstream wger TLS cert. Use this
    # when wger sits behind an internal CA (e.g. Step CA) the container does
    # not trust by default. None → verify against the system/certifi bundle.
    wger_ca_bundle: str | None = None

    # ---------- inbound auth strategy ----------
    mcp_auth: AuthStrategy = AuthStrategy.api_key

    # api_key strategy
    mcp_api_keys: list[str] = Field(default_factory=list)
    mcp_api_key_header: str = "X-API-Key"

    # jwt strategy
    mcp_jwt_jwks_uri: HttpUrl | None = None
    mcp_jwt_issuer: str | None = None
    mcp_jwt_audience: str | None = None
    mcp_jwt_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    mcp_jwt_username_claim: str = "sub"
    mcp_jwt_allowed_users: list[str] = Field(default_factory=list)
    mcp_jwt_jwks_ttl_seconds: int = 3600

    # proxy_header strategy
    mcp_proxy_user_header: str = "X-Remote-User"
    mcp_proxy_email_header: str | None = None
    mcp_proxy_trusted_ips: list[str] = Field(default_factory=list)
    mcp_proxy_allowed_users: list[str] = Field(default_factory=list)

    # ---------- transport ----------
    host: str = "0.0.0.0"
    port: int = 8765
    mcp_path: str = "/mcp"

    # DNS rebinding protection. Empty list disables the check.
    allowed_hosts: list[str] = Field(default_factory=list)

    @field_validator("mcp_jwt_algorithms", mode="after")
    @classmethod
    def _normalize_algs(cls, v: list[str]) -> list[str]:
        return [a.strip().upper() for a in v if a.strip()]

    @property
    def wger_api_root(self) -> str:
        return str(self.wger_base_url).rstrip("/") + "/api/v2"


def _csv_to_json_list(name: str) -> None:
    """Allow comma-separated values for list-typed env vars."""
    import os

    if name not in os.environ:
        return
    raw = os.environ[name].strip()
    if not raw:
        os.environ[name] = "[]"
        return
    if raw.startswith("["):
        return
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    os.environ[name] = "[" + ",".join(f'"{p}"' for p in parts) + "]"


_CSV_VARS = (
    "MCP_API_KEYS",
    "MCP_JWT_ALGORITHMS",
    "MCP_JWT_ALLOWED_USERS",
    "MCP_PROXY_TRUSTED_IPS",
    "MCP_PROXY_ALLOWED_USERS",
    "ALLOWED_HOSTS",
)


def load_settings() -> Settings:
    for var in _CSV_VARS:
        _csv_to_json_list(var)
    return Settings()  # type: ignore[call-arg]
