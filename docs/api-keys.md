# API keys & tokens

`wger-mcp` deals with **two** different credentials. They are not interchangeable. Mixing them up is the #1 cause of `401 Unauthorized` when first setting the server up.

|Credential|Direction|Used by|Strategy|
|----------|---------|-------|--------|
|`MCP_API_KEYS`|inbound (client → MCP)|MCP clients (Claude Desktop/Code, curl, custom apps)|`api_key`|
|`WGER_API_TOKEN`|outbound (MCP → wger)|`wger-mcp` itself when calling wger REST|always|

Never expose `WGER_API_TOKEN` to a client. Never let `WGER_API_TOKEN` end up in a header that leaves the MCP container.

---

## `MCP_API_KEYS` — inbound auth (clients → MCP)

Used only when `MCP_AUTH=api_key`. The MCP server accepts requests that present **any** of the configured keys in one of these headers:

- `Authorization: Bearer <key>`
- `<MCP_API_KEY_HEADER>: <key>` (default: `X-API-Key`)

### Generate an MCP key

```bash
openssl rand -hex 32
```

32 bytes of entropy, hex-encoded → 64 ASCII chars. The same generator is fine for token-rotation and per-client keys.

### Configure

Single key:

```ini
MCP_AUTH=api_key
MCP_API_KEYS=c77a3c30459ca1a287edbef527d31ebf17072bfaede3bc835a0275bdae2bd08d
```

Multiple keys (one per client, or rotation overlap):

```ini
MCP_API_KEYS=key-for-claude-desktop,key-for-cli-script,key-being-rotated-out
```

Comma-separated. **Any** of them is accepted; the server doesn't tell apart which one was used in logs (only the first 6 chars of a *rejected* key are logged, never the accepted one).

Custom header name:

```ini
MCP_API_KEY_HEADER=X-Wger-Key
```

Useful when a reverse proxy strips or overrides `X-API-Key`.

### Rotate without downtime

1. Generate a new key:

   ```bash
   NEW=$(openssl rand -hex 32)
   ```

2. Append it to `MCP_API_KEYS` alongside the old one:

   ```ini
   MCP_API_KEYS=<old-key>,<NEW>
   ```

3. Restart the container so the new env is picked up:

   ```bash
   docker restart wger-mcp
   ```

4. Update all clients to send `<NEW>`. Watch logs for any rejected keys still using the old value.
5. When you're sure no client uses the old key, remove it from `MCP_API_KEYS` and restart again.

### Use in clients

Claude Code (per-user scope):

```bash
claude mcp add wger \
  --transport http \
  --header "X-API-Key: $MCP_API_KEY" \
  --scope user \
  -- https://wger.example.com/mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "wger": {
      "type": "streamable-http",
      "url": "https://wger.example.com/mcp",
      "headers": { "X-API-Key": "..." }
    }
  }
}
```

curl smoke test:

```bash
curl -fsS -X POST https://wger.example.com/mcp \
  -H "X-API-Key: $MCP_API_KEY" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

---

## `WGER_API_TOKEN` — outbound to wger

This is wger's own Django REST Framework token. `wger-mcp` uses it to talk to `https://<wger>/api/v2/*`. There is one token per wger user; all MCP tool calls run as that user.

### Generate a wger token

1. Sign in to the wger UI.
2. Go to **User menu → Settings → API**.
3. Click **Generate new API key**.

The token is shown only once. Copy and paste into the env file:

```ini
WGER_BASE_URL=https://wger.example.com
WGER_API_TOKEN=<paste-here>
```

### Revoke

In the same wger UI screen. Generating a new token immediately invalidates the previous one. Update `WGER_API_TOKEN` and restart `wger-mcp`.

### Test outside MCP

```bash
curl -fsS https://wger.example.com/api/v2/userprofile/ \
  -H "Authorization: Token $WGER_API_TOKEN"
```

200 with your profile JSON → token works. 401 → token invalid; regenerate.

---

## Other auth strategies (no `MCP_API_KEYS` involved)

When `MCP_AUTH` is `jwt` or `proxy_header`, the inbound auth uses different credentials and `MCP_API_KEYS` is ignored.

- `jwt`: clients send a Bearer JWT from your OIDC provider. The token is validated against the provider's JWKS — `wger-mcp` does not issue or store JWTs. See [README.md](../README.md#2-jwt--any-oidcoauth2-provider).
- `proxy_header`: an upstream reverse proxy authenticates the user (Authelia, Authentik, oauth2-proxy, …) and forwards an identity header. See [README.md](../README.md#3-proxy_header--sit-behind-your-existing-sso-proxy).

Regardless of the inbound strategy, `WGER_API_TOKEN` is always required for outbound calls to wger.

---

## FAQ

### Why generate `MCP_API_KEYS` at all? Can I just use the wger token for both?

Technically yes — set `MCP_API_KEYS=$WGER_API_TOKEN` and the server will accept it. But you give up a few useful properties:

1. **Scope separation.** A wger DRF token grants full account access. An MCP API key is only "is this client allowed to talk to MCP?". The MCP server can add rate-limiting, auditing, validation, future per-key tool allowlists, etc. — none of which a wger token has.
2. **Per-client rotation.** Want one key for Claude Desktop, another for a CLI script, another for a teammate? Comma-separate them in `MCP_API_KEYS`, revoke any one independently. wger has no concept of per-client sub-tokens — revoking the wger token revokes everything.
3. **Defense in depth.** Clients only learn the MCP key. The wger token never leaves the MCP container. A compromised client cannot bypass MCP and hit `/api/v2/*` directly with the same credential.
4. **Auth strategy swappability.** Switching `MCP_AUTH` to `jwt` or `proxy_header` requires no change to upstream credentials — `WGER_API_TOKEN` stays put. If your inbound auth *was* the wger token, every client would have to re-credential at the same time.
5. **Auditability.** Logs of rejected keys identify the misconfigured client; multiple accepted keys let you correlate traffic to a specific client.

For a single-user, single-client homelab the simplification is real and the risk is small — but the cost of generating a second key is zero (`openssl rand -hex 32`), so the default recommendation is to keep them separate.

### Do I need to use `api_key` at all?

No. `MCP_AUTH=jwt` (any OIDC provider) and `MCP_AUTH=proxy_header` (sit behind an SSO reverse proxy) are first-class alternatives. Pick whatever fits your existing identity infrastructure. `WGER_API_TOKEN` is still required, regardless.

### Where do these credentials live in deployment?

Both go in the env file passed to the container (`env_file:` in compose, or `--env-file` on `docker run`). Never in the image, never in source control.

## Security checklist

- [ ] `.env` is in `.gitignore` and never committed. Use `.env.example` for templates.
- [ ] Keys are at least 32 bytes of entropy (`openssl rand -hex 32`). Don't reuse passwords.
- [ ] Different keys for different clients (so revoking one doesn't lock everyone out).
- [ ] `ALLOWED_HOSTS` is set to your public hostname in production. Empty list is fine in dev but disables DNS-rebinding protection.
- [ ] Always front the server with HTTPS in production. Plain HTTP leaks `MCP_API_KEYS` and `Authorization: Bearer` over the wire.
- [ ] When using `MCP_AUTH=proxy_header`, set `MCP_PROXY_TRUSTED_IPS` to the *exact* peer addresses of your reverse proxy. `X-Forwarded-For` is not honored — only the immediate TCP peer.
- [ ] Rotate `WGER_API_TOKEN` if you suspect it leaked. There's no per-token scope in wger; a leaked token grants full account access.
