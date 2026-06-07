"""Workout log tools (per-set logging + legacy workouts)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..wger_client import WgerClient, WgerError
from .common import bad_request, err


def register(mcp: FastMCP, client: WgerClient) -> None:
    @mcp.tool()
    async def list_workouts(
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
    ) -> list[dict[str, Any]]:
        """List legacy workout plans."""
        try:
            return await client.paginate("workout/", limit=limit)
        except WgerError as exc:
            return [err(exc)]

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
            "repetitions": reps,
            "weight": weight_kg,
            "date": (workout_log_date or date.today()).isoformat(),
        }
        if rir is not None:
            payload["rir"] = rir
        try:
            return await client.post("workoutlog/", json=payload)
        except WgerError as exc:
            return err(exc)

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
            # workoutlog.date is a full datetime in this wger version, so a
            # date-only `date__lte=<day>` resolves to 00:00:00 and silently
            # drops every log on date_to itself. Bound on the next midnight so
            # the whole date_to day is included.
            params["date__lte"] = (date_to + timedelta(days=1)).isoformat()
        if exercise_id is not None:
            params["exercise"] = exercise_id
        try:
            return await client.paginate("workoutlog/", params=params, limit=limit)
        except WgerError as exc:
            return [err(exc)]

    @mcp.tool()
    async def get_workout_log(log_id: int) -> dict[str, Any]:
        """Fetch one workout log entry."""
        try:
            return await client.get(f"workoutlog/{log_id}/")
        except WgerError as exc:
            return err(exc)

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
            payload["repetitions"] = reps
        if weight_kg is not None:
            payload["weight"] = weight_kg
        if rir is not None:
            payload["rir"] = rir
        if when is not None:
            payload["date"] = when.isoformat()
        if not payload:
            return bad_request("no fields to update")
        try:
            return await client.patch(f"workoutlog/{log_id}/", json=payload)
        except WgerError as exc:
            return err(exc)

    @mcp.tool()
    async def delete_workout_log(log_id: int) -> dict[str, Any]:
        """Delete a workout log entry."""
        try:
            await client.delete(f"workoutlog/{log_id}/")
            return {"deleted": True, "log_id": log_id}
        except WgerError as exc:
            return err(exc)
