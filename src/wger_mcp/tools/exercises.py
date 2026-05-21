"""Exercise / ingredient catalog tools (read-only lookups)."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..wger_client import WgerClient, WgerError
from .common import err


def register(mcp: FastMCP, client: WgerClient) -> None:
    @mcp.tool()
    async def search_exercises(
        query: Annotated[str, Field(min_length=2)],
        language: Annotated[str, Field(pattern=r"^[a-z]{2}$")] = "en",
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> list[dict[str, Any]]:
        """Search the wger exercise database by name. Language is ISO 639-1 ('en','pl',...)."""
        try:
            results = await client.paginate(
                "exerciseinfo/",
                params={"name__search": query, "language__code": language},
                limit=limit,
            )
        except WgerError as exc:
            return [err(exc)]
        q_lower = query.lower()
        shaped: list[dict[str, Any]] = []
        for ex in results:
            if not isinstance(ex, dict):
                continue
            translations = [
                t for t in (ex.get("translations") or []) if isinstance(t, dict) and t.get("name")
            ]
            match = next(
                (t for t in translations if q_lower in (t.get("name") or "").lower()),
                translations[0] if translations else None,
            )
            shaped.append({
                "id": ex.get("id"),
                "uuid": ex.get("uuid"),
                "name": (match or {}).get("name"),
                "category": (ex.get("category") or {}).get("name"),
                "equipment": [e.get("name") for e in (ex.get("equipment") or [])],
                "translations": [
                    {"language": t.get("language"), "name": t.get("name")} for t in translations
                ],
            })
        return shaped

    @mcp.tool()
    async def get_exercise(exercise_id: int) -> dict[str, Any]:
        """Fetch full exercise detail (instructions, muscles, equipment)."""
        try:
            return await client.get(f"exerciseinfo/{exercise_id}/")
        except WgerError as exc:
            return err(exc)

    @mcp.tool()
    async def search_ingredients(
        query: Annotated[str, Field(min_length=2)],
        language: Annotated[str, Field(pattern=r"^[a-z]{2}$")] = "en",
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> list[dict[str, Any]]:
        """Search wger's ingredient database (foods with macros)."""
        try:
            results = await client.paginate(
                "ingredientinfo/",
                params={"name__search": query, "language__code": language},
                limit=limit,
            )
        except WgerError as exc:
            return [err(exc)]
        shaped: list[dict[str, Any]] = []
        for ing in results:
            if not isinstance(ing, dict):
                continue
            shaped.append({
                "id": ing.get("id"),
                "uuid": ing.get("uuid"),
                "name": ing.get("name"),
                "energy": ing.get("energy"),
                "protein": ing.get("protein"),
                "carbohydrates": ing.get("carbohydrates"),
                "fat": ing.get("fat"),
                "brand": ing.get("brand"),
            })
        return shaped

    @mcp.tool()
    async def get_ingredient(ingredient_id: int) -> dict[str, Any]:
        """Fetch full ingredient detail (macros per 100 g, brand, etc.)."""
        try:
            return await client.get(f"ingredient/{ingredient_id}/")
        except WgerError as exc:
            return err(exc)

    @mcp.tool()
    async def search_ingredient_by_barcode(
        barcode: Annotated[str, Field(min_length=4, max_length=32)],
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> list[dict[str, Any]]:
        """Look up ingredients by EAN/UPC barcode (exact match on the wger
        `code` field). Typically returns 0 or 1 result — much more precise
        than name search."""
        try:
            results = await client.paginate(
                "ingredient/", params={"code": barcode}, limit=limit
            )
        except WgerError as exc:
            return [err(exc)]
        shaped: list[dict[str, Any]] = []
        for ing in results:
            if not isinstance(ing, dict):
                continue
            shaped.append({
                "id": ing.get("id"),
                "uuid": ing.get("uuid"),
                "name": ing.get("name"),
                "code": ing.get("code"),
                "brand": ing.get("brand"),
                "energy": ing.get("energy"),
                "protein": ing.get("protein"),
                "carbohydrates": ing.get("carbohydrates"),
                "fat": ing.get("fat"),
            })
        return shaped

    @mcp.tool()
    async def list_categories(
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        """List exercise categories (Chest, Back, …)."""
        try:
            return await client.paginate("exercisecategory/", limit=limit)
        except WgerError as exc:
            return [err(exc)]

    @mcp.tool()
    async def list_equipment(
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        """List exercise equipment (Dumbbell, Barbell, …)."""
        try:
            return await client.paginate("equipment/", limit=limit)
        except WgerError as exc:
            return [err(exc)]

    @mcp.tool()
    async def list_muscles(
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        """List muscles."""
        try:
            return await client.paginate("muscle/", limit=limit)
        except WgerError as exc:
            return [err(exc)]

    @mcp.tool()
    async def search_exercises_by_filter(
        equipment_id: int | None = None,
        muscle_id: int | None = None,
        category_id: int | None = None,
        language: Annotated[str, Field(pattern=r"^[a-z]{2}$")] = "en",
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> list[dict[str, Any]]:
        """Find exercises by structured filters (e.g. Dumbbell + Back)."""
        params: dict[str, Any] = {"language__code": language}
        if equipment_id is not None:
            params["equipment"] = equipment_id
        if muscle_id is not None:
            params["muscles"] = muscle_id
        if category_id is not None:
            params["category"] = category_id
        try:
            results = await client.paginate("exerciseinfo/", params=params, limit=limit)
        except WgerError as exc:
            return [err(exc)]
        shaped: list[dict[str, Any]] = []
        for ex in results:
            if not isinstance(ex, dict):
                continue
            translations = [
                t for t in (ex.get("translations") or []) if isinstance(t, dict) and t.get("name")
            ]
            shaped.append({
                "id": ex.get("id"),
                "uuid": ex.get("uuid"),
                "name": (translations[0].get("name") if translations else None),
                "category": (ex.get("category") or {}).get("name"),
                "equipment": [e.get("name") for e in (ex.get("equipment") or [])],
                "muscles": [m.get("name") for m in (ex.get("muscles") or [])],
            })
        return shaped
