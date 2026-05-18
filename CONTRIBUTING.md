# Contributing to wger-mcp

Thanks for your interest! This project is intended to live in the wger ecosystem; until it moves there, contributions are accepted here.

## Development setup

```bash
uv sync --dev
uv run pytest        # full suite (28 tests)
uv run ruff check
```

## Project layout

```
src/wger_mcp/
├── server.py          # FastMCP server + tool definitions
├── wger_client.py     # thin async httpx wrapper around the wger REST API
├── config.py          # pydantic-settings; auth strategy selector
└── auth/
    ├── __init__.py    # build_auth_middleware factory
    ├── base.py        # shared helpers, NoAuthMiddleware
    ├── api_key.py     # static shared secret
    ├── jwt.py         # generic OIDC JWT via JWKS
    └── proxy_header.py# trusted-reverse-proxy header (mirrors wger AUTH_PROXY)
```

## Adding a new tool

Tools live in `server.py`. Each is an `async def` decorated with `@mcp.tool()` with type-annotated parameters (FastMCP turns these into the MCP tool schema automatically). Wrap upstream calls in `try/except WgerError` and return `_err(exc)` so failures reach the MCP client as a structured payload rather than an exception.

## Adding a new auth strategy

1. Add a value to the `AuthStrategy` enum in `config.py`.
2. Add per-strategy settings to `Settings` (prefix `MCP_<STRATEGY>_*`).
3. Create `src/wger_mcp/auth/<name>.py` with a middleware class that:
   - bypasses `/health` (use `is_bypass_path`)
   - on success calls `set_identity(scope, strategy=..., user=...)`
   - on failure calls `reply_unauthorized(...)`
4. Wire it into `auth/__init__.py`'s factory.
5. Add tests under `tests/test_auth_<name>.py`. The `make_client(**env)` helper in `conftest.py` rebuilds the app under the given env.

## Tests

- All tests run via Starlette's `TestClient`; `_ClientIPOverride` (conftest) lets you inject a peer IP for the `proxy_header` strategy.
- `respx` is used to mock outbound HTTP — both wger API calls and JWKS fetches.
- The MCP `initialize` request is the cheapest way to exercise the full middleware chain end-to-end.

## License

AGPL-3.0-or-later, to match the wger project. By contributing you agree your code is licensed under the same terms.
