"""Thin async wrapper around wger REST API v2."""

from __future__ import annotations

from typing import Any

import httpx


class WgerError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"wger api error {status}: {body}")
        self.status = status
        self.body = body


class WgerClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Token {token}",
                "Accept": "application/json",
                "User-Agent": "wger-mcp/0.1",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> WgerClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        resp = await self._client.request(method, path.lstrip("/"), **kw)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise WgerError(resp.status_code, body)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict | None = None) -> Any:
        return await self._request("POST", path, json=json)

    async def patch(self, path: str, json: dict | None = None) -> Any:
        return await self._request("PATCH", path, json=json)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    async def paginate(self, path: str, params: dict | None = None, *, limit: int = 100) -> list:
        """Walk DRF pagination ('next' URLs) until limit items or end."""
        out: list = []
        first = await self.get(path, params=params)
        if not isinstance(first, dict) or "results" not in first:
            return first if isinstance(first, list) else [first]
        out.extend(first["results"])
        next_url = first.get("next")
        while next_url and len(out) < limit:
            resp = await self._client.get(next_url)
            if resp.status_code >= 400:
                break
            page = resp.json()
            out.extend(page.get("results", []))
            next_url = page.get("next")
        return out[:limit]
