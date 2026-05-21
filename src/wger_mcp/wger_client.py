"""Thin async wrapper around wger REST API v2."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

PAGINATE_CONCURRENCY = 8


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
        """Collect up to ``limit`` items across DRF pages.

        Fast path: use ``count`` from the first response plus the ``next`` URL
        (``?page=`` or ``?limit=&offset=``) to fan out the remaining pages
        concurrently. Fallback: serial ``next``-walking when the pagination
        scheme can't be inferred.
        """
        first = await self.get(path, params=params)
        if not isinstance(first, dict) or "results" not in first:
            return first if isinstance(first, list) else [first]
        results: list = list(first["results"])
        next_url = first.get("next")
        if not next_url or len(results) >= limit:
            return results[:limit]

        page_size = len(first["results"])
        extra_urls = _plan_remaining_pages(
            next_url, first.get("count"), page_size, limit, already=len(results)
        )
        if extra_urls is None:
            return await self._paginate_serial(next_url, results, limit)
        if not extra_urls:
            return results[:limit]

        sem = asyncio.Semaphore(PAGINATE_CONCURRENCY)

        async def _fetch(url: str) -> list:
            async with sem:
                resp = await self._client.get(url)
            if resp.status_code >= 400:
                return []
            data = resp.json()
            return data.get("results", []) if isinstance(data, dict) else []

        pages = await asyncio.gather(*[_fetch(u) for u in extra_urls])
        for page in pages:
            results.extend(page)
        return results[:limit]

    async def _paginate_serial(self, next_url: str, results: list, limit: int) -> list:
        while next_url and len(results) < limit:
            resp = await self._client.get(next_url)
            if resp.status_code >= 400:
                break
            page = resp.json()
            results.extend(page.get("results", []))
            next_url = page.get("next")
        return results[:limit]


def _plan_remaining_pages(
    next_url: str,
    count: int | None,
    page_size: int,
    limit: int,
    *,
    already: int,
) -> list[str] | None:
    """Build URLs for remaining pages, or return None if the pagination
    scheme is unknown (caller should fall back to serial next-walking)."""
    if not count or page_size <= 0:
        return None
    parts = urlsplit(next_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "offset" in query:
        key, stride = "offset", page_size
        try:
            start = int(query["offset"])
        except ValueError:
            return None
    elif "page" in query:
        key, stride = "page", 1
        try:
            start = int(query["page"])
        except ValueError:
            return None
    else:
        return None
    needed = max(0, min(limit, count) - already)
    if needed <= 0:
        return []
    n_pages = (needed + page_size - 1) // page_size
    urls: list[str] = []
    for i in range(n_pages):
        q = {**query, key: str(start + i * stride)}
        urls.append(
            urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
        )
    return urls
