#!/usr/bin/env python3
"""Fetch an OAuth2 access token for testing the MCP server's `jwt` auth strategy.

Provider-agnostic: works against any OIDC provider that publishes
``/.well-known/openid-configuration`` (Keycloak, Authentik, Auth0, Okta, ...).

Modes:
  - device   : OAuth 2.0 Device Authorization Grant (no password in CLI)
  - password : Resource Owner Password Credentials (quick, requires public client)

Usage:
  uv run python scripts/get_token.py device \\
      --issuer https://idp/realms/x --client wger-mcp
  uv run python scripts/get_token.py password \\
      --issuer https://idp/realms/x --client wger-mcp --user alice
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from urllib.parse import urljoin

import httpx


def _well_known(issuer: str) -> dict:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    r = httpx.get(url, timeout=10.0, verify=os.environ.get("SSL_VERIFY", "1") != "0")
    r.raise_for_status()
    return r.json()


def device_flow(issuer: str, client_id: str, scope: str) -> dict:
    conf = _well_known(issuer)
    device_endpoint = conf.get("device_authorization_endpoint") or urljoin(
        conf["token_endpoint"].rsplit("/", 1)[0] + "/", "auth/device"
    )
    token_endpoint = conf["token_endpoint"]

    r = httpx.post(
        device_endpoint,
        data={"client_id": client_id, "scope": scope},
        timeout=15.0,
        verify=os.environ.get("SSL_VERIFY", "1") != "0",
    )
    r.raise_for_status()
    init = r.json()
    verify_url = init.get("verification_uri_complete") or init["verification_uri"]
    print("Open this URL and approve:", verify_url)
    print("User code:", init["user_code"])

    interval = init.get("interval", 5)
    deadline = time.time() + init.get("expires_in", 600)
    while time.time() < deadline:
        time.sleep(interval)
        tr = httpx.post(
            token_endpoint,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": init["device_code"],
            },
            timeout=15.0,
            verify=os.environ.get("SSL_VERIFY", "1") != "0",
        )
        if tr.status_code == 200:
            return tr.json()
        err = tr.json().get("error")
        if err in ("authorization_pending", "slow_down"):
            if err == "slow_down":
                interval += 5
            continue
        raise SystemExit(f"device flow failed: {tr.status_code} {tr.text}")
    raise SystemExit("device code expired")


def password_flow(issuer: str, client_id: str, user: str, password: str, scope: str) -> dict:
    conf = _well_known(issuer)
    r = httpx.post(
        conf["token_endpoint"],
        data={
            "grant_type": "password",
            "client_id": client_id,
            "username": user,
            "password": password,
            "scope": scope,
        },
        timeout=15.0,
        verify=os.environ.get("SSL_VERIFY", "1") != "0",
    )
    if r.status_code != 200:
        raise SystemExit(f"password flow failed: {r.status_code} {r.text}")
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["device", "password"])
    ap.add_argument(
        "--issuer",
        default=os.environ.get("MCP_JWT_ISSUER"),
        help="OIDC issuer URL (or set MCP_JWT_ISSUER)",
    )
    ap.add_argument("--client", required=True, help="OAuth2 client_id")
    ap.add_argument("--user", help="username (password flow)")
    ap.add_argument("--scope", default="openid profile email")
    ap.add_argument(
        "--export",
        action="store_true",
        help="emit `export MCP_TOKEN=...` line for shell sourcing",
    )
    args = ap.parse_args()
    if not args.issuer:
        sys.exit("--issuer is required (or set MCP_JWT_ISSUER)")

    if args.mode == "device":
        tok = device_flow(args.issuer, args.client, args.scope)
    else:
        if not args.user:
            sys.exit("--user is required for password mode")
        pw = os.environ.get("KC_PASSWORD") or getpass.getpass("password: ")
        tok = password_flow(args.issuer, args.client, args.user, pw, args.scope)

    if args.export:
        print(f"export MCP_TOKEN={tok['access_token']}")
    else:
        print(json.dumps(tok, indent=2))


if __name__ == "__main__":
    main()
