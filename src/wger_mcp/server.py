"""FastMCP server: wger tools exposed over streamable HTTP with pluggable auth."""

from __future__ import annotations

import contextlib
import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated, Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .auth import build_auth_middleware
from .config import Settings, load_settings
from .wger_client import WgerClient, WgerError

log = logging.getLogger("wger_mcp")


def build_app(settings: Settings) -> Starlette:
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(settings.allowed_hosts),
        allowed_hosts=settings.allowed_hosts,
    )
    mcp = FastMCP(
        "wger",
        json_response=True,
        streamable_http_path="/",
        transport_security=transport_security,
    )

    client = WgerClient(settings.wger_api_root, settings.wger_api_token)

    def _err(exc: WgerError) -> dict[str, Any]:
        return {"error": True, "status": exc.status, "detail": exc.body}

    @mcp.tool()
    async def whoami() -> dict[str, Any]:
        """Return the wger user profile bound to the configured API token."""
        try:
            return await client.get("userprofile/")
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def list_routines(
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
    ) -> list[dict[str, Any]]:
        """List the user's training routines (new wger model)."""
        try:
            return await client.paginate("routine/", limit=limit)
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def get_routine(routine_id: int) -> dict[str, Any]:
        """Fetch a single routine with its day structure."""
        try:
            return await client.get(f"routine/{routine_id}/")
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def list_routine_days(
        routine_id: int,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> list[dict[str, Any]]:
        """List training days of a routine."""
        try:
            return await client.paginate(
                "day/", params={"routine": routine_id, "ordering": "order"}, limit=limit
            )
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def get_routine_day(day_id: int) -> dict[str, Any]:
        """Fetch a single training day."""
        try:
            return await client.get(f"day/{day_id}/")
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def list_slots(
        day_id: int,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> list[dict[str, Any]]:
        """List slots in a training day."""
        try:
            return await client.paginate(
                "slot/", params={"day": day_id, "ordering": "order"}, limit=limit
            )
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def list_slot_entries(
        slot_id: int,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> list[dict[str, Any]]:
        """List exercise entries in a slot."""
        try:
            return await client.paginate(
                "slot-entry/", params={"slot": slot_id, "ordering": "order"}, limit=limit
            )
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def get_slot_entry(entry_id: int) -> dict[str, Any]:
        """Fetch a slot entry. Note: per-set sets/reps/weight/rir/rest are stored
        on separate *-config endpoints linked by slot_entry, not on the entry
        itself. Use list_slot_entry_configs to read them."""
        try:
            return await client.get(f"slot-entry/{entry_id}/")
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def list_slot_entry_configs(
        slot_entry_id: int,
        kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch per-iteration configs for a slot entry. kinds filters which
        ones to read (e.g. ['sets','reps','weight']); default = all 10."""
        out: dict[str, Any] = {"slot_entry_id": slot_entry_id}
        targets = kinds or list(_SLOT_CONFIG_PATHS.keys())
        for kind in targets:
            path = _SLOT_CONFIG_PATHS.get(kind)
            if not path:
                out[kind] = {"error": True, "detail": f"unknown kind '{kind}'"}
                continue
            try:
                out[kind] = await client.paginate(
                    path, params={"slot_entry": slot_entry_id, "ordering": "iteration"}, limit=200
                )
            except WgerError as exc:
                out[kind] = _err(exc)
        return out

    @mcp.tool()
    async def create_routine(
        name: Annotated[str, Field(min_length=1, max_length=255)],
        description: str = "",
        start: date | None = None,
        end: date | None = None,
        fit_in_week: bool = False,
    ) -> dict[str, Any]:
        """Create a training routine. Start defaults to today."""
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "start": (start or date.today()).isoformat(),
            "fit_in_week": fit_in_week,
        }
        if end is not None:
            payload["end"] = end.isoformat()
        try:
            return await client.post("routine/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def update_routine(
        routine_id: int,
        name: str | None = None,
        description: str | None = None,
        start: date | None = None,
        end: date | None = None,
        fit_in_week: bool | None = None,
    ) -> dict[str, Any]:
        """Patch a routine. Only provided fields are sent."""
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if start is not None:
            payload["start"] = start.isoformat()
        if end is not None:
            payload["end"] = end.isoformat()
        if fit_in_week is not None:
            payload["fit_in_week"] = fit_in_week
        if not payload:
            return {"error": True, "status": 400, "detail": "no fields to update"}
        try:
            return await client.patch(f"routine/{routine_id}/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def add_routine_day(
        routine_id: int,
        name: Annotated[str, Field(min_length=1, max_length=255)],
        order: Annotated[int, Field(ge=1, le=100)],
        description: str = "",
        is_rest: bool = False,
        day_type: str = "standard",
    ) -> dict[str, Any]:
        """Add a training day to a routine."""
        payload: dict[str, Any] = {
            "routine": routine_id,
            "order": order,
            "name": name,
            "description": description,
            "is_rest": is_rest,
            "type": day_type,
        }
        try:
            return await client.post("day/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def add_slot_to_day(
        day_id: int,
        order: Annotated[int, Field(ge=1, le=100)],
        sets: Annotated[int | None, Field(ge=1, le=50)] = None,
        rest_seconds: Annotated[int | None, Field(ge=0, le=3600)] = None,
    ) -> dict[str, Any]:
        """Add an exercise slot (grouping) to a day."""
        payload: dict[str, Any] = {"day": day_id, "order": order}
        if sets is not None:
            payload["sets"] = sets
        if rest_seconds is not None:
            payload["rest"] = rest_seconds
        try:
            return await client.post("slot/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def update_routine_day(
        day_id: int,
        name: str | None = None,
        order: Annotated[int | None, Field(ge=1, le=100)] = None,
        description: str | None = None,
        is_rest: bool | None = None,
        day_type: str | None = None,
    ) -> dict[str, Any]:
        """Patch a training day. Only provided fields are sent."""
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if order is not None:
            payload["order"] = order
        if description is not None:
            payload["description"] = description
        if is_rest is not None:
            payload["is_rest"] = is_rest
        if day_type is not None:
            payload["type"] = day_type
        if not payload:
            return {"error": True, "status": 400, "detail": "no fields to update"}
        try:
            return await client.patch(f"day/{day_id}/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def update_slot(
        slot_id: int,
        order: Annotated[int | None, Field(ge=1, le=100)] = None,
        sets: Annotated[int | None, Field(ge=1, le=50)] = None,
        rest_seconds: Annotated[int | None, Field(ge=0, le=3600)] = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Patch a slot."""
        payload: dict[str, Any] = {}
        if order is not None:
            payload["order"] = order
        if sets is not None:
            payload["sets"] = sets
        if rest_seconds is not None:
            payload["rest"] = rest_seconds
        if comment is not None:
            payload["comment"] = comment
        if not payload:
            return {"error": True, "status": 400, "detail": "no fields to update"}
        try:
            return await client.patch(f"slot/{slot_id}/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def update_slot_entry(
        slot_entry_id: int,
        exercise_id: int | None = None,
        order: Annotated[int | None, Field(ge=1, le=100)] = None,
        comment: str | None = None,
        repetition_unit: int | None = None,
        weight_unit: int | None = None,
    ) -> dict[str, Any]:
        """Patch a slot entry (the exercise binding)."""
        payload: dict[str, Any] = {}
        if exercise_id is not None:
            payload["exercise"] = exercise_id
        if order is not None:
            payload["order"] = order
        if comment is not None:
            payload["comment"] = comment
        if repetition_unit is not None:
            payload["repetition_unit"] = repetition_unit
        if weight_unit is not None:
            payload["weight_unit"] = weight_unit
        if not payload:
            return {"error": True, "status": 400, "detail": "no fields to update"}
        try:
            return await client.patch(f"slot-entry/{slot_entry_id}/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def update_slot_entry_config(
        kind: str,
        config_id: int,
        value: float | None = None,
        iteration: Annotated[int | None, Field(ge=1, le=1000)] = None,
        operation: str | None = None,
        step: str | None = None,
        repeat: bool | None = None,
    ) -> dict[str, Any]:
        """Patch an existing per-iteration config record.
        kind selects the endpoint (sets, reps, weight, rir, rest, max_*).
        Use this to bump weight when progressing."""
        path = _SLOT_CONFIG_PATHS.get(kind)
        if not path:
            return {
                "error": True,
                "status": 400,
                "detail": f"unknown kind '{kind}'; expected one of {sorted(_SLOT_CONFIG_PATHS)}",
            }
        payload: dict[str, Any] = {}
        if value is not None:
            payload["value"] = value
        if iteration is not None:
            payload["iteration"] = iteration
        if operation is not None:
            payload["operation"] = operation
        if step is not None:
            payload["step"] = step
        if repeat is not None:
            payload["repeat"] = repeat
        if not payload:
            return {"error": True, "status": 400, "detail": "no fields to update"}
        try:
            return await client.patch(f"{path}{config_id}/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def delete_slot_entry_config(kind: str, config_id: int) -> dict[str, Any]:
        """Delete a per-iteration config record."""
        path = _SLOT_CONFIG_PATHS.get(kind)
        if not path:
            return {
                "error": True,
                "status": 400,
                "detail": f"unknown kind '{kind}'; expected one of {sorted(_SLOT_CONFIG_PATHS)}",
            }
        try:
            await client.delete(f"{path}{config_id}/")
            return {"deleted": True, "kind": kind, "config_id": config_id}
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def delete_routine(routine_id: int) -> dict[str, Any]:
        """Delete a routine and its entire day/slot/entry tree."""
        try:
            await client.delete(f"routine/{routine_id}/")
            return {"deleted": True, "routine_id": routine_id}
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def delete_routine_day(day_id: int) -> dict[str, Any]:
        """Delete a training day (cascades to its slots and entries)."""
        try:
            await client.delete(f"day/{day_id}/")
            return {"deleted": True, "day_id": day_id}
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def delete_slot(slot_id: int) -> dict[str, Any]:
        """Delete a slot (cascades to its entries and configs)."""
        try:
            await client.delete(f"slot/{slot_id}/")
            return {"deleted": True, "slot_id": slot_id}
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def delete_slot_entry(slot_entry_id: int) -> dict[str, Any]:
        """Delete a slot entry (the exercise binding) and its configs."""
        try:
            await client.delete(f"slot-entry/{slot_entry_id}/")
            return {"deleted": True, "slot_entry_id": slot_entry_id}
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def attach_exercise_to_slot(
        slot_id: int,
        exercise_id: int,
        order: Annotated[int, Field(ge=1, le=100)] = 1,
        repetition_unit: int | None = None,
        weight_unit: int | None = None,
        comment: str = "",
    ) -> dict[str, Any]:
        """Attach an exercise to a slot. exercise_id is the numeric wger PK
        (same id used in log_set / exerciseinfo). Per-set reps/weight live on
        sets-config / repetitions-config / weight-config records, not here."""
        payload: dict[str, Any] = {
            "slot": slot_id,
            "exercise": exercise_id,
            "order": order,
            "comment": comment,
        }
        if repetition_unit is not None:
            payload["repetition_unit"] = repetition_unit
        if weight_unit is not None:
            payload["weight_unit"] = weight_unit
        try:
            return await client.post("slot-entry/", json=payload)
        except WgerError as exc:
            return _err(exc)

    _SLOT_CONFIG_PATHS = {
        "sets": "sets-config/",
        "reps": "repetitions-config/",
        "weight": "weight-config/",
        "rir": "rir-config/",
        "rest": "rest-config/",
        "max_sets": "max-sets-config/",
        "max_reps": "max-repetitions-config/",
        "max_weight": "max-weight-config/",
        "max_rir": "max-rir-config/",
        "max_rest": "max-rest-config/",
    }

    @mcp.tool()
    async def set_slot_entry_config(
        slot_entry_id: int,
        kind: str,
        value: float,
        iteration: Annotated[int, Field(ge=1, le=1000)] = 1,
        operation: str = "r",
        step: str = "abs",
        repeat: bool = False,
    ) -> dict[str, Any]:
        """Create a per-iteration config record for a slot entry.
        kind: one of sets, reps, weight, rir, rest, max_sets, max_reps,
        max_weight, max_rir, max_rest. operation 'r' = replace, 'a' = add,
        's' = subtract. step 'abs' or 'percent'."""
        path = _SLOT_CONFIG_PATHS.get(kind)
        if not path:
            return {
                "error": True,
                "status": 400,
                "detail": f"unknown kind '{kind}'; expected one of {sorted(_SLOT_CONFIG_PATHS)}",
            }
        payload: dict[str, Any] = {
            "slot_entry": slot_entry_id,
            "iteration": iteration,
            "value": value,
            "operation": operation,
            "step": step,
            "repeat": repeat,
        }
        try:
            return await client.post(path, json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def add_exercise_with_sets(
        day_id: int,
        exercise_id: int,
        sets: Annotated[int, Field(ge=1, le=50)],
        reps: Annotated[int, Field(ge=1, le=1000)],
        weight_kg: Annotated[float, Field(ge=0, le=1000)],
        slot_order: Annotated[int, Field(ge=1, le=100)] = 1,
        rest_seconds: Annotated[int | None, Field(ge=0, le=3600)] = None,
    ) -> dict[str, Any]:
        """High-level convenience: create slot + slot-entry + sets/reps/weight
        configs in one call. Returns the created ids. Partial failures are
        reported in the response."""
        result: dict[str, Any] = {}
        slot_payload: dict[str, Any] = {"day": day_id, "order": slot_order}
        if rest_seconds is not None:
            slot_payload["rest"] = rest_seconds
        try:
            slot = await client.post("slot/", json=slot_payload)
        except WgerError as exc:
            return _err(exc) | {"stage": "slot"}
        result["slot"] = slot
        slot_id = slot.get("id") if isinstance(slot, dict) else None
        if not slot_id:
            return result | {"error": True, "stage": "slot", "detail": "missing slot id"}

        try:
            entry = await client.post(
                "slot-entry/",
                json={"slot": slot_id, "exercise": exercise_id, "order": 1, "comment": ""},
            )
        except WgerError as exc:
            return result | _err(exc) | {"stage": "slot-entry"}
        result["slot_entry"] = entry
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if not entry_id:
            return result | {"error": True, "stage": "slot-entry", "detail": "missing entry id"}

        for kind, value in (("sets", sets), ("reps", reps), ("weight", weight_kg)):
            try:
                result[f"{kind}_config"] = await client.post(
                    _SLOT_CONFIG_PATHS[kind],
                    json={
                        "slot_entry": entry_id,
                        "iteration": 1,
                        "value": value,
                        "operation": "r",
                        "step": "abs",
                        "repeat": False,
                    },
                )
            except WgerError as exc:
                return result | _err(exc) | {"stage": f"{kind}-config"}

        return result

    @mcp.tool()
    async def list_workouts(
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
    ) -> list[dict[str, Any]]:
        """List legacy workout plans."""
        try:
            return await client.paginate("workout/", limit=limit)
        except WgerError as exc:
            return [_err(exc)]

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
            return [_err(exc)]
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
    async def log_set(
        exercise_id: int,
        reps: Annotated[int, Field(ge=1, le=1000)],
        weight_kg: Annotated[float, Field(ge=0, le=1000)],
        workout_log_date: date | None = None,
        rir: Annotated[float | None, Field(ge=0, le=10)] = None,
    ) -> dict[str, Any]:
        """Log a completed set (workoutlog). Uses today if no date given."""
        payload = {
            "exercise": exercise_id,
            "reps": reps,
            "weight": weight_kg,
            "date": (workout_log_date or date.today()).isoformat(),
        }
        if rir is not None:
            payload["rir"] = rir
        try:
            return await client.post("workoutlog/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def list_workout_logs(
        date_from: date | None = None,
        date_to: date | None = None,
        exercise_id: int | None = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    ) -> list[dict[str, Any]]:
        """List workout log entries (individual sets) with optional date/exercise filters."""
        params: dict[str, Any] = {"ordering": "-date"}
        if date_from is not None:
            params["date__gte"] = date_from.isoformat()
        if date_to is not None:
            params["date__lte"] = date_to.isoformat()
        if exercise_id is not None:
            params["exercise"] = exercise_id
        try:
            return await client.paginate("workoutlog/", params=params, limit=limit)
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def get_workout_log(log_id: int) -> dict[str, Any]:
        """Fetch one workout log entry."""
        try:
            return await client.get(f"workoutlog/{log_id}/")
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def update_workout_log(
        log_id: int,
        reps: Annotated[int | None, Field(ge=1, le=1000)] = None,
        weight_kg: Annotated[float | None, Field(ge=0, le=1000)] = None,
        rir: Annotated[float | None, Field(ge=0, le=10)] = None,
        when: date | None = None,
    ) -> dict[str, Any]:
        """Patch a workout log entry. Only provided fields are sent."""
        payload: dict[str, Any] = {}
        if reps is not None:
            payload["reps"] = reps
        if weight_kg is not None:
            payload["weight"] = weight_kg
        if rir is not None:
            payload["rir"] = rir
        if when is not None:
            payload["date"] = when.isoformat()
        if not payload:
            return {"error": True, "status": 400, "detail": "no fields to update"}
        try:
            return await client.patch(f"workoutlog/{log_id}/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def delete_workout_log(log_id: int) -> dict[str, Any]:
        """Delete a workout log entry."""
        try:
            await client.delete(f"workoutlog/{log_id}/")
            return {"deleted": True, "log_id": log_id}
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def log_body_weight(
        weight_kg: Annotated[float, Field(gt=0, le=500)],
        when: date | None = None,
    ) -> dict[str, Any]:
        """Log a body-weight entry."""
        payload = {
            "weight": weight_kg,
            "date": (when or date.today()).isoformat(),
        }
        try:
            return await client.post("weightentry/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def get_body_weight_history(
        limit: Annotated[int, Field(ge=1, le=500)] = 30,
    ) -> list[dict[str, Any]]:
        """Return recent body-weight entries (newest first)."""
        try:
            return await client.paginate(
                "weightentry/", params={"ordering": "-date"}, limit=limit
            )
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def update_body_weight_entry(
        entry_id: int,
        weight_kg: Annotated[float | None, Field(gt=0, le=500)] = None,
        when: date | None = None,
    ) -> dict[str, Any]:
        """Patch a body-weight entry."""
        payload: dict[str, Any] = {}
        if weight_kg is not None:
            payload["weight"] = weight_kg
        if when is not None:
            payload["date"] = when.isoformat()
        if not payload:
            return {"error": True, "status": 400, "detail": "no fields to update"}
        try:
            return await client.patch(f"weightentry/{entry_id}/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def delete_body_weight_entry(entry_id: int) -> dict[str, Any]:
        """Delete a body-weight entry."""
        try:
            await client.delete(f"weightentry/{entry_id}/")
            return {"deleted": True, "entry_id": entry_id}
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def list_nutrition_plans(
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> list[dict[str, Any]]:
        """List your nutrition plans."""
        try:
            return await client.paginate("nutritionplan/", limit=limit)
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def get_nutrition_plan(plan_id: int) -> dict[str, Any]:
        """Fetch one nutrition plan with meals and items."""
        try:
            return await client.get(f"nutritionplan/{plan_id}/")
        except WgerError as exc:
            return _err(exc)

    @mcp.tool()
    async def get_exercise(exercise_id: int) -> dict[str, Any]:
        """Fetch full exercise detail (instructions, muscles, equipment)."""
        try:
            return await client.get(f"exerciseinfo/{exercise_id}/")
        except WgerError as exc:
            return _err(exc)

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
            return [_err(exc)]
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
    async def weekly_summary(
        days: Annotated[int, Field(ge=1, le=90)] = 7,
    ) -> dict[str, Any]:
        """Aggregate workoutlog over the last N days: sets/reps/volume per exercise."""
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        try:
            logs = await client.paginate(
                "workoutlog/",
                params={"date__gte": since, "ordering": "date"},
                limit=1000,
            )
        except WgerError as exc:
            return _err(exc)

        per_exercise: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"sets": 0, "reps": 0, "volume_kg": 0.0, "dates": set()}
        )
        for entry in logs:
            ex_id = entry.get("exercise") or entry.get("exercise_base")
            if ex_id is None:
                continue
            reps = entry.get("reps") or 0
            try:
                weight = float(entry.get("weight") or 0)
            except (TypeError, ValueError):
                weight = 0.0
            bucket = per_exercise[ex_id]
            bucket["sets"] += 1
            bucket["reps"] += reps
            bucket["volume_kg"] += reps * weight
            if d := entry.get("date"):
                bucket["dates"].add(d)

        breakdown = [
            {
                "exercise_id": ex_id,
                "sets": v["sets"],
                "reps": v["reps"],
                "volume_kg": round(v["volume_kg"], 2),
                "active_days": len(v["dates"]),
            }
            for ex_id, v in sorted(
                per_exercise.items(), key=lambda kv: kv[1]["volume_kg"], reverse=True
            )
        ]
        return {
            "since": since,
            "until": date.today().isoformat(),
            "total_sets": sum(v["sets"] for v in per_exercise.values()),
            "total_volume_kg": round(sum(v["volume_kg"] for v in per_exercise.values()), 2),
            "exercises": breakdown,
        }

    @mcp.tool()
    async def exercise_history(
        exercise_id: int,
        days: Annotated[int, Field(ge=1, le=730)] = 90,
        limit: Annotated[int, Field(ge=1, le=2000)] = 500,
    ) -> dict[str, Any]:
        """Return chronological workout-log entries for one exercise over the
        last N days. Includes per-session aggregates."""
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        try:
            logs = await client.paginate(
                "workoutlog/",
                params={"exercise": exercise_id, "date__gte": since, "ordering": "date"},
                limit=limit,
            )
        except WgerError as exc:
            return _err(exc)
        sessions: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"sets": 0, "reps": 0, "volume_kg": 0.0, "top_weight": 0.0, "entries": []}
        )
        for entry in logs:
            try:
                weight = float(entry.get("weight") or 0)
            except (TypeError, ValueError):
                weight = 0.0
            reps = entry.get("reps") or 0
            d = entry.get("date") or ""
            b = sessions[d]
            b["sets"] += 1
            b["reps"] += reps
            b["volume_kg"] += reps * weight
            b["top_weight"] = max(b["top_weight"], weight)
            b["entries"].append({
                "id": entry.get("id"),
                "reps": reps,
                "weight": weight,
                "rir": entry.get("rir"),
            })
        return {
            "exercise_id": exercise_id,
            "since": since,
            "until": date.today().isoformat(),
            "total_sets": sum(s["sets"] for s in sessions.values()),
            "sessions": [
                {
                    "date": d,
                    **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()},
                }
                for d, s in sorted(sessions.items())
            ],
        }

    @mcp.tool()
    async def personal_records(
        exercise_id: int | None = None,
        days: Annotated[int, Field(ge=1, le=3650)] = 730,
    ) -> dict[str, Any]:
        """Compute PRs from workout logs: max weight, max reps, best
        Epley-estimated 1RM. If exercise_id is omitted, returns one record
        block per exercise."""
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        params: dict[str, Any] = {"date__gte": since, "ordering": "date"}
        if exercise_id is not None:
            params["exercise"] = exercise_id
        try:
            logs = await client.paginate("workoutlog/", params=params, limit=5000)
        except WgerError as exc:
            return _err(exc)

        def _epley(weight: float, reps: int) -> float:
            return weight * (1 + reps / 30) if reps > 0 else 0.0

        per_ex: dict[int, dict[str, Any]] = {}
        for entry in logs:
            ex_id = entry.get("exercise") or entry.get("exercise_base")
            if ex_id is None:
                continue
            try:
                weight = float(entry.get("weight") or 0)
            except (TypeError, ValueError):
                weight = 0.0
            reps = entry.get("reps") or 0
            est_1rm = _epley(weight, reps)
            rec = per_ex.setdefault(
                ex_id,
                {
                    "exercise_id": ex_id,
                    "max_weight": {"value": 0.0, "reps": 0, "date": None, "log_id": None},
                    "max_reps": {"value": 0, "weight": 0.0, "date": None, "log_id": None},
                    "best_est_1rm": {
                        "value": 0.0,
                        "weight": 0.0,
                        "reps": 0,
                        "date": None,
                        "log_id": None,
                    },
                },
            )
            if weight > rec["max_weight"]["value"]:
                rec["max_weight"] = {
                    "value": weight,
                    "reps": reps,
                    "date": entry.get("date"),
                    "log_id": entry.get("id"),
                }
            if reps > rec["max_reps"]["value"]:
                rec["max_reps"] = {
                    "value": reps,
                    "weight": weight,
                    "date": entry.get("date"),
                    "log_id": entry.get("id"),
                }
            if est_1rm > rec["best_est_1rm"]["value"]:
                rec["best_est_1rm"] = {
                    "value": round(est_1rm, 2),
                    "weight": weight,
                    "reps": reps,
                    "date": entry.get("date"),
                    "log_id": entry.get("id"),
                }

        return {
            "since": since,
            "until": date.today().isoformat(),
            "records": sorted(
                per_ex.values(),
                key=lambda r: r["best_est_1rm"]["value"],
                reverse=True,
            ),
        }

    @mcp.tool()
    async def nutrition_summary(
        when: date | None = None,
        plan_id: int | None = None,
    ) -> dict[str, Any]:
        """Sum kcal/protein/carbs/fat from diary entries for a date. Per entry,
        fetches the ingredient's macros (per 100 g) and scales by amount_g."""
        target = (when or date.today()).isoformat()
        params: dict[str, Any] = {"datetime__date": target}
        if plan_id is not None:
            params["plan"] = plan_id
        try:
            entries = await client.paginate("nutritiondiary/", params=params, limit=500)
        except WgerError as exc:
            return _err(exc)

        totals = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        items: list[dict[str, Any]] = []
        cache: dict[int, dict[str, Any]] = {}
        for e in entries:
            ing_id = e.get("ingredient")
            amount = float(e.get("amount") or 0)
            if not ing_id or amount <= 0:
                continue
            if ing_id not in cache:
                try:
                    cache[ing_id] = await client.get(f"ingredient/{ing_id}/")
                except WgerError as exc:
                    cache[ing_id] = {"_err": _err(exc)}
            ing = cache[ing_id]
            if "_err" in ing:
                items.append({
                    "entry_id": e.get("id"),
                    "ingredient_id": ing_id,
                    "error": ing["_err"],
                })
                continue
            factor = amount / 100.0
            kcal = float(ing.get("energy") or 0) * factor
            prot = float(ing.get("protein") or 0) * factor
            carb = float(ing.get("carbohydrates") or 0) * factor
            fat = float(ing.get("fat") or 0) * factor
            totals["kcal"] += kcal
            totals["protein_g"] += prot
            totals["carbs_g"] += carb
            totals["fat_g"] += fat
            items.append({
                "entry_id": e.get("id"),
                "ingredient_id": ing_id,
                "ingredient_name": ing.get("name"),
                "amount_g": amount,
                "kcal": round(kcal, 1),
                "protein_g": round(prot, 1),
                "carbs_g": round(carb, 1),
                "fat_g": round(fat, 1),
            })
        return {
            "date": target,
            "totals": {k: round(v, 1) for k, v in totals.items()},
            "items": items,
        }

    _VOLUME_METRICS = ("volume", "sets", "reps", "top_weight", "est_1rm")
    _GROUP_BY_OPTIONS = ("none", "exercise", "muscle", "category")

    def _bucket_start(d: date, bucket: str) -> str:
        if bucket == "day":
            return d.isoformat()
        if bucket == "week":
            return (d - timedelta(days=d.weekday())).isoformat()
        if bucket == "month":
            return d.replace(day=1).isoformat()
        raise ValueError(f"unknown bucket {bucket}")

    def _groups_for(ex_id: int, group_by: str, ex_cache: dict[int, dict[str, Any]]):
        if group_by == "none":
            return [None]
        info = ex_cache.get(ex_id) or {}
        if group_by == "exercise":
            trs = info.get("translations") or []
            label = next(
                (t.get("name") for t in trs if isinstance(t, dict) and t.get("name")),
                f"Exercise {ex_id}",
            )
            return [(ex_id, label)]
        if group_by == "category":
            cat = info.get("category") or {}
            return [(cat.get("id") or 0, cat.get("name") or "Unknown")]
        if group_by == "muscle":
            muscles = info.get("muscles") or []
            if not muscles:
                return [(0, "Unknown")]
            return [(m.get("id") or 0, m.get("name") or "Unknown") for m in muscles]
        return [None]

    async def _load_ex_meta(
        log_entries: list[dict[str, Any]], group_by: str
    ) -> dict[int, dict[str, Any]]:
        if group_by == "none":
            return {}
        ex_ids: set[int] = set()
        for e in log_entries:
            eid = e.get("exercise") or e.get("exercise_base")
            if isinstance(eid, int):
                ex_ids.add(eid)
        cache: dict[int, dict[str, Any]] = {}
        for eid in ex_ids:
            try:
                cache[eid] = await client.get(f"exerciseinfo/{eid}/")
            except WgerError:
                cache[eid] = {}
        return cache

    def _new_metric_bucket() -> dict[str, float]:
        return {"volume_kg": 0.0, "sets": 0, "reps": 0, "top_weight": 0.0, "est_1rm": 0.0}

    def _accumulate(bucket: dict[str, float], reps: int, weight: float) -> None:
        bucket["volume_kg"] += reps * weight
        bucket["sets"] += 1
        bucket["reps"] += reps
        if weight > bucket["top_weight"]:
            bucket["top_weight"] = weight
        epley = weight * (1 + reps / 30) if reps > 0 else 0.0
        if epley > bucket["est_1rm"]:
            bucket["est_1rm"] = epley

    def _project(bucket: dict[str, float], selected: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "volume" in selected:
            out["volume_kg"] = round(bucket["volume_kg"], 2)
        if "sets" in selected:
            out["sets"] = bucket["sets"]
        if "reps" in selected:
            out["reps"] = bucket["reps"]
        if "top_weight" in selected:
            out["top_weight"] = round(bucket["top_weight"], 2)
        if "est_1rm" in selected:
            out["est_1rm"] = round(bucket["est_1rm"], 2)
        return out

    @mcp.tool()
    async def volume_trend(
        days: Annotated[int, Field(ge=1, le=730)] = 60,
        bucket: str = "week",
        metrics: list[str] | None = None,
        group_by: str = "none",
        exercise_id: int | None = None,
    ) -> dict[str, Any]:
        """Time-bucketed training volume. bucket=day|week|month. group_by=
        none|exercise|muscle|category. For muscle, exercise volume is
        attributed to each primary muscle (sum per-muscle > global)."""
        if bucket not in ("day", "week", "month"):
            return {"error": True, "status": 400, "detail": "bucket must be day|week|month"}
        if group_by not in _GROUP_BY_OPTIONS:
            return {
                "error": True,
                "status": 400,
                "detail": f"group_by must be one of {list(_GROUP_BY_OPTIONS)}",
            }
        valid = set(_VOLUME_METRICS)
        selected = [m for m in (metrics or list(_VOLUME_METRICS)) if m in valid]
        if not selected:
            selected = list(_VOLUME_METRICS)
        since = date.today() - timedelta(days=days - 1)
        params: dict[str, Any] = {"date__gte": since.isoformat(), "ordering": "date"}
        if exercise_id is not None:
            params["exercise"] = exercise_id
        try:
            logs = await client.paginate("workoutlog/", params=params, limit=5000)
        except WgerError as exc:
            return _err(exc)

        ex_cache = await _load_ex_meta(logs, group_by)
        buckets: dict[tuple, dict[str, float]] = defaultdict(_new_metric_bucket)
        for entry in logs:
            ex_id = entry.get("exercise") or entry.get("exercise_base")
            d_str = entry.get("date")
            if ex_id is None or not d_str:
                continue
            try:
                weight = float(entry.get("weight") or 0)
            except (TypeError, ValueError):
                weight = 0.0
            reps = entry.get("reps") or 0
            try:
                d = date.fromisoformat(d_str)
            except ValueError:
                continue
            bkt = _bucket_start(d, bucket)
            for group in _groups_for(ex_id, group_by, ex_cache):
                key = (bkt, group)
                _accumulate(buckets[key], reps, weight)

        series: list[dict[str, Any]] = []
        for (bkt, group), m in sorted(
            buckets.items(), key=lambda kv: (kv[0][0], -kv[1]["volume_kg"])
        ):
            row: dict[str, Any] = {"bucket_start": bkt}
            if group_by != "none" and group is not None:
                row["group"] = {"key": group[0], "label": group[1]}
            row.update(_project(m, selected))
            series.append(row)

        return {
            "since": since.isoformat(),
            "until": date.today().isoformat(),
            "bucket": bucket,
            "group_by": group_by,
            "metrics": selected,
            "series": series,
        }

    @mcp.tool()
    async def compare_periods(
        window_days: Annotated[int, Field(ge=1, le=365)] = 7,
        gap_days: Annotated[int, Field(ge=0, le=365)] = 0,
        metrics: list[str] | None = None,
        group_by: str = "none",
    ) -> dict[str, Any]:
        """Compare two consecutive rolling windows. Period A = last
        `window_days` (ending today). Period B = same length, shifted back by
        `window_days + gap_days`."""
        if group_by not in _GROUP_BY_OPTIONS:
            return {
                "error": True,
                "status": 400,
                "detail": f"group_by must be one of {list(_GROUP_BY_OPTIONS)}",
            }
        valid = set(_VOLUME_METRICS)
        selected = [m for m in (metrics or list(_VOLUME_METRICS)) if m in valid]
        if not selected:
            selected = list(_VOLUME_METRICS)

        today = date.today()
        a_to = today
        a_from = today - timedelta(days=window_days - 1)
        b_to = a_from - timedelta(days=1 + gap_days)
        b_from = b_to - timedelta(days=window_days - 1)

        try:
            logs = await client.paginate(
                "workoutlog/",
                params={
                    "date__gte": b_from.isoformat(),
                    "date__lte": a_to.isoformat(),
                    "ordering": "date",
                },
                limit=5000,
            )
        except WgerError as exc:
            return _err(exc)

        ex_cache = await _load_ex_meta(logs, group_by)
        per_period: dict[str, dict[tuple | None, dict[str, float]]] = {
            "a": defaultdict(_new_metric_bucket),
            "b": defaultdict(_new_metric_bucket),
        }
        totals: dict[str, dict[str, float]] = {"a": _new_metric_bucket(), "b": _new_metric_bucket()}
        for entry in logs:
            ex_id = entry.get("exercise") or entry.get("exercise_base")
            d_str = entry.get("date")
            if ex_id is None or not d_str:
                continue
            try:
                d = date.fromisoformat(d_str)
            except ValueError:
                continue
            if a_from <= d <= a_to:
                period = "a"
            elif b_from <= d <= b_to:
                period = "b"
            else:
                continue
            try:
                weight = float(entry.get("weight") or 0)
            except (TypeError, ValueError):
                weight = 0.0
            reps = entry.get("reps") or 0
            _accumulate(totals[period], reps, weight)
            for group in _groups_for(ex_id, group_by, ex_cache):
                _accumulate(per_period[period][group], reps, weight)

        # Union of groups seen in either period
        all_groups: set[tuple | None] = set(per_period["a"].keys()) | set(per_period["b"].keys())

        def _delta(a: dict[str, float], b: dict[str, float]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            if "volume" in selected:
                out["volume_kg"] = round(a["volume_kg"] - b["volume_kg"], 2)
            if "sets" in selected:
                out["sets"] = a["sets"] - b["sets"]
            if "reps" in selected:
                out["reps"] = a["reps"] - b["reps"]
            if "top_weight" in selected:
                out["top_weight"] = round(a["top_weight"] - b["top_weight"], 2)
            if "est_1rm" in selected:
                out["est_1rm"] = round(a["est_1rm"] - b["est_1rm"], 2)
            return out

        def _delta_pct(a: dict[str, float], b: dict[str, float]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for short, full in (
                ("volume", "volume_kg"),
                ("sets", "sets"),
                ("reps", "reps"),
                ("top_weight", "top_weight"),
                ("est_1rm", "est_1rm"),
            ):
                if short not in selected:
                    continue
                base = b[full]
                if base == 0:
                    out[full] = None
                else:
                    out[full] = round(((a[full] - base) / base) * 100, 1)
            return out

        comparison: list[dict[str, Any]] = []
        for group in all_groups:
            a = per_period["a"].get(group) or _new_metric_bucket()
            b = per_period["b"].get(group) or _new_metric_bucket()
            row: dict[str, Any] = {}
            if group_by != "none" and group is not None:
                row["group"] = {"key": group[0], "label": group[1]}
            row["a"] = _project(a, selected)
            row["b"] = _project(b, selected)
            row["delta"] = _delta(a, b)
            row["delta_pct"] = _delta_pct(a, b)
            comparison.append(row)
        comparison.sort(
            key=lambda r: abs(r["delta"].get("volume_kg") or 0),
            reverse=True,
        )

        return {
            "period_a": {"from": a_from.isoformat(), "to": a_to.isoformat()},
            "period_b": {"from": b_from.isoformat(), "to": b_to.isoformat()},
            "group_by": group_by,
            "metrics": selected,
            "total_a": _project(totals["a"], selected),
            "total_b": _project(totals["b"], selected),
            "total_delta": _delta(totals["a"], totals["b"]),
            "total_delta_pct": _delta_pct(totals["a"], totals["b"]),
            "comparison": comparison,
        }

    @mcp.tool()
    async def list_categories(
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        """List exercise categories (Chest, Back, …)."""
        try:
            return await client.paginate("exercisecategory/", limit=limit)
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def list_equipment(
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        """List exercise equipment (Dumbbell, Barbell, …)."""
        try:
            return await client.paginate("equipment/", limit=limit)
        except WgerError as exc:
            return [_err(exc)]

    @mcp.tool()
    async def list_muscles(
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        """List muscles."""
        try:
            return await client.paginate("muscle/", limit=limit)
        except WgerError as exc:
            return [_err(exc)]

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
            return [_err(exc)]
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

    @mcp.tool()
    async def log_ingredient(
        plan_id: int,
        ingredient_id: int,
        amount_g: Annotated[float, Field(gt=0, le=10000)],
        when: date | None = None,
    ) -> dict[str, Any]:
        """Log eaten food against a plan (logitem)."""
        payload = {
            "plan": plan_id,
            "ingredient": ingredient_id,
            "amount": amount_g,
            "datetime": f"{(when or date.today()).isoformat()}T12:00:00Z",
        }
        try:
            return await client.post("nutritiondiary/", json=payload)
        except WgerError as exc:
            return _err(exc)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await client.aclose()

    async def healthcheck(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    routes = [
        Route("/health", healthcheck),
        Mount(settings.mcp_path, app=mcp.streamable_http_app()),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    # Keep `/mcp` and `/mcp/` both as MCP entry points instead of issuing a 307
    # from one to the other — MCP clients (and curl) do not follow redirects on POST.
    app.router.redirect_slashes = False
    auth_cls, auth_kwargs = build_auth_middleware(settings)
    app.add_middleware(auth_cls, **auth_kwargs)
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    log.info("MCP_AUTH=%s, MCP_PATH=%s", settings.mcp_auth.value, settings.mcp_path)
    app = build_app(settings)
    # forwarded_allow_ips="*" so uvicorn trusts X-Forwarded-Proto / -For from any
    # peer. Required when running behind a reverse proxy on a separate IP (the
    # default whitelist of 127.0.0.1 silently ignores headers from nginx etc).
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
