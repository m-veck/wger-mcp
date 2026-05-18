"""Tests for WgerClient: pagination, auth header, error mapping."""

from __future__ import annotations

import pytest
import respx

from wger_mcp.wger_client import WgerClient, WgerError

BASE = "https://wger.test/api/v2"


@pytest.mark.asyncio
async def test_auth_header_sent() -> None:
    async with WgerClient(BASE, "my-token") as c:
        with respx.mock(base_url=BASE) as router:
            route = router.get("/userprofile/").respond(json={"id": 1})
            await c.get("userprofile/")
            assert route.call_count == 1
            sent = route.calls.last.request
            assert sent.headers["authorization"] == "Token my-token"


@pytest.mark.asyncio
async def test_error_raises_wger_error() -> None:
    async with WgerClient(BASE, "t") as c, respx.mock(base_url=BASE) as router:
        router.get("/userprofile/").respond(status_code=403, json={"detail": "nope"})
        with pytest.raises(WgerError) as exc:
            await c.get("userprofile/")
        assert exc.value.status == 403
        assert exc.value.body == {"detail": "nope"}


@pytest.mark.asyncio
async def test_paginate_walks_next() -> None:
    async with WgerClient(BASE, "t") as c, respx.mock() as router:
        router.get(f"{BASE}/workoutlog/", params={"page": "2"}).respond(
            json={
                "count": 3,
                "next": None,
                "previous": f"{BASE}/workoutlog/",
                "results": [{"id": 3}],
            }
        )
        router.get(f"{BASE}/workoutlog/").respond(
            json={
                "count": 3,
                "next": f"{BASE}/workoutlog/?page=2",
                "previous": None,
                "results": [{"id": 1}, {"id": 2}],
            }
        )
        out = await c.paginate("workoutlog/")
        assert [r["id"] for r in out] == [1, 2, 3]


@pytest.mark.asyncio
async def test_paginate_respects_limit() -> None:
    async with WgerClient(BASE, "t") as c, respx.mock() as router:
        router.get(f"{BASE}/workoutlog/").respond(
            json={"results": [{"id": i} for i in range(5)], "next": None}
        )
        out = await c.paginate("workoutlog/", limit=3)
        assert len(out) == 3


@pytest.mark.asyncio
async def test_204_returns_none() -> None:
    async with WgerClient(BASE, "t") as c, respx.mock(base_url=BASE) as router:
        router.delete("/weightentry/42/").respond(status_code=204)
        result = await c.delete("weightentry/42/")
        assert result is None
