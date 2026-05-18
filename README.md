# wger-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes the [wger](https://wger.de) fitness/nutrition REST API as tools (workouts, routines, exercise search, nutrition diary, body-weight tracking, weekly volume aggregation, …) so that AI assistants can read and write your wger data.

- **Transport:** MCP **Streamable HTTP** (FastMCP).
- **Inbound auth:** pluggable — static API key, generic OIDC JWT, or trusted reverse-proxy header. No vendor lock-in to a specific IdP.
- **Outbound auth:** wger DRF API token (single-user model).

## Quick start

```bash
uv sync
cp .env.example .env
# Edit .env: set WGER_BASE_URL, WGER_API_TOKEN, and one auth strategy.
uv run wger-mcp
```

Server listens on `http://0.0.0.0:8765`, MCP endpoint at `/mcp`.

## Inbound auth strategies

Pick one with `MCP_AUTH=`. The server gates **every** request to `/mcp/*` according to that strategy. `/health` is always public.

### 1. `api_key` — simplest, recommended for personal / single-user

A static shared secret. Generated once and stored on every client.

```ini
MCP_AUTH=api_key
MCP_API_KEYS=$(openssl rand -hex 32)
# Optional, default is X-API-Key
MCP_API_KEY_HEADER=X-API-Key
```

Clients send it as either:

- `Authorization: Bearer <key>`
- `X-API-Key: <key>` (or your custom header name)

Multiple keys can be configured (rotation, multiple clients) by passing comma-separated values.

### 2. `jwt` — any OIDC/OAuth2 provider

Validates a Bearer JWT against a JWKS endpoint. Provider-agnostic.

```ini
MCP_AUTH=jwt
MCP_JWT_JWKS_URI=https://idp.example.com/.well-known/jwks.json
MCP_JWT_ISSUER=https://idp.example.com
MCP_JWT_AUDIENCE=wger-mcp                 # optional
MCP_JWT_ALGORITHMS=RS256                  # comma-separated, default RS256
MCP_JWT_USERNAME_CLAIM=preferred_username # which claim names the user
MCP_JWT_ALLOWED_USERS=alice,bob           # optional allowlist
```

Verified: signature (via JWKS), `iss`, `exp`, and `aud` if `MCP_JWT_AUDIENCE` is set. JWKS is cached for `MCP_JWT_JWKS_TTL_SECONDS` (default 3600 s) and re-fetched on signature failure to handle key rotation.

Provider examples:

| Provider | `MCP_JWT_JWKS_URI` | `MCP_JWT_USERNAME_CLAIM` |
|----------|--------------------|--------------------------|
| Keycloak | `https://<host>/realms/<realm>/protocol/openid-connect/certs` | `preferred_username` |
| Authentik | `https://<host>/application/o/<slug>/jwks/` | `preferred_username` |
| Authelia (OIDC) | `https://<host>/jwks.json` | `preferred_username` |
| Auth0 | `https://<tenant>.auth0.com/.well-known/jwks.json` | `sub` |
| Okta | `https://<tenant>.okta.com/oauth2/default/v1/keys` | `sub` |
| AWS Cognito | `https://cognito-idp.<region>.amazonaws.com/<pool>/.well-known/jwks.json` | `cognito:username` |

### 3. `proxy_header` — sit behind your existing SSO proxy

Mirrors wger's own [AUTH_PROXY_HEADER](https://wger-project.github.io/docs/administration/auth_proxy.html) model. A reverse proxy (nginx, Caddy, Apache, Traefik) authenticates the user (Authelia, Authentik, oauth2-proxy in front of any OIDC IdP, LDAP, SAML, mutual TLS, …) and forwards an identity header.

```ini
MCP_AUTH=proxy_header
MCP_PROXY_USER_HEADER=X-Remote-User
MCP_PROXY_EMAIL_HEADER=X-Remote-Email     # optional
MCP_PROXY_TRUSTED_IPS=127.0.0.1,10.0.0.0/8
MCP_PROXY_ALLOWED_USERS=alice             # optional allowlist
```

Safety: requests are accepted **only** when the immediate peer IP (`scope['client']`) is in `MCP_PROXY_TRUSTED_IPS`. If you have additional proxies in front (CDN, k8s ingress, …), terminate that chain so the trusted proxy is the direct peer. `X-Forwarded-For` is intentionally not consulted.

### 4. `none` — local dev only

Disables auth entirely. The server logs a warning at startup. Do not expose to a network.

```ini
MCP_AUTH=none
```

## Tools

| Tool | Description |
|------|-------------|
| `whoami` | Show wger user profile bound to the configured API token |
| `list_routines` / `get_routine` | New-model training routines |
| `list_workouts` | Legacy workout plans |
| `search_exercises(query, language, limit)` | Find exercises by name (ISO 639-1 language code) |
| `get_exercise(id)` | Full exercise detail: muscles, equipment, instructions |
| `log_set(exercise_id, reps, weight_kg, date?, rir?)` | Add a workout log entry |
| `log_body_weight(weight_kg, when?)` | Body-weight entry |
| `get_body_weight_history(limit)` | Recent weight entries |
| `list_nutrition_plans` / `get_nutrition_plan(id)` | Nutrition plans |
| `search_ingredients(query, language, limit)` | Find foods with macros |
| `log_ingredient(plan_id, ingredient_id, amount_g, when?)` | Nutrition diary entry |
| `weekly_summary(days)` | Aggregate workoutlog: sets, reps, volume per exercise |

## Configuring a client

### Claude Desktop / Code (Streamable HTTP), `api_key`

```json
{
  "mcpServers": {
    "wger": {
      "type": "streamable-http",
      "url": "https://wger-mcp.example.com/mcp",
      "headers": {
        "X-API-Key": "<your-key>"
      }
    }
  }
}
```

### Claude Desktop / Code, `jwt`

Obtain a token from your IdP (device code, password, refresh, …) and pass it as `Authorization: Bearer <token>`. See `scripts/get_token.py` for a Keycloak device-flow example.

## Deployment

A reference Docker setup ships in `Dockerfile` and `compose.example.yml`. The server is a single ASGI app (`wger_mcp.server:build_app`) and can also be run under any ASGI host (Hypercorn, Granian, gunicorn-uvicorn, …).

If exposed over HTTPS via a reverse proxy, configure the proxy with:

```nginx
proxy_buffering off;
proxy_request_buffering off;
proxy_read_timeout 3600s;
```

so that streamable-HTTP/SSE responses aren't buffered.

## Development

```bash
uv sync --dev
uv run pytest        # 28 tests covering all 3 auth strategies + wger client
uv run ruff check
```

## License

AGPL-3.0-or-later — matches the wger project. See [LICENSE](LICENSE).
