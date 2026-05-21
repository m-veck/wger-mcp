"""Analytics tools: weekly summary, exercise history, PRs, volume trend, compare."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..wger_client import WgerClient, WgerError
from .common import bad_request, err

VOLUME_METRICS: tuple[str, ...] = ("volume", "sets", "reps", "top_weight", "est_1rm")
GROUP_BY_OPTIONS: tuple[str, ...] = ("none", "exercise", "muscle", "category")

# Exercise metadata (name/category/muscles) is effectively static per wger
# deployment; cache it process-wide across tool invocations.
_EX_META_CACHE: dict[int, dict[str, Any]] = {}
_EX_META_CONCURRENCY = 8


def _epley(weight: float, reps: int) -> float:
    return weight * (1 + reps / 30) if reps > 0 else 0.0


def _bucket_start(d: date, bucket: str) -> str:
    if bucket == "day":
        return d.isoformat()
    if bucket == "week":
        return (d - timedelta(days=d.weekday())).isoformat()
    if bucket == "month":
        return d.replace(day=1).isoformat()
    raise ValueError(f"unknown bucket {bucket}")


def _groups_for(
    ex_id: int, group_by: str, ex_cache: dict[int, dict[str, Any]]
) -> list[tuple[int, str] | None]:
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
    client: WgerClient, log_entries: list[dict[str, Any]], group_by: str
) -> dict[int, dict[str, Any]]:
    if group_by == "none":
        return {}
    ex_ids: set[int] = set()
    for entry in log_entries:
        eid = entry.get("exercise") or entry.get("exercise_base")
        if isinstance(eid, int):
            ex_ids.add(eid)
    missing = [eid for eid in ex_ids if eid not in _EX_META_CACHE]
    if missing:
        sem = asyncio.Semaphore(_EX_META_CONCURRENCY)

        async def _fetch(eid: int) -> tuple[int, dict[str, Any]]:
            async with sem:
                try:
                    return eid, await client.get(f"exerciseinfo/{eid}/")
                except WgerError:
                    return eid, {}

        for eid, meta in await asyncio.gather(*[_fetch(e) for e in missing]):
            _EX_META_CACHE[eid] = meta
    return {eid: _EX_META_CACHE[eid] for eid in ex_ids}


def _new_metric_bucket() -> dict[str, float]:
    return {"volume_kg": 0.0, "sets": 0, "reps": 0, "top_weight": 0.0, "est_1rm": 0.0}


def _accumulate(bucket: dict[str, float], reps: int, weight: float) -> None:
    bucket["volume_kg"] += reps * weight
    bucket["sets"] += 1
    bucket["reps"] += reps
    if weight > bucket["top_weight"]:
        bucket["top_weight"] = weight
    est = _epley(weight, reps)
    if est > bucket["est_1rm"]:
        bucket["est_1rm"] = est


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


def _select_metrics(metrics: list[str] | None) -> list[str]:
    valid = set(VOLUME_METRICS)
    selected = [m for m in (metrics or list(VOLUME_METRICS)) if m in valid]
    return selected or list(VOLUME_METRICS)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def register(mcp: FastMCP, client: WgerClient) -> None:
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
            return err(exc)

        per_exercise: dict[int, dict[str, Any]] = defaultdict(
            lambda: {"sets": 0, "reps": 0, "volume_kg": 0.0, "dates": set()}
        )
        for entry in logs:
            ex_id = entry.get("exercise") or entry.get("exercise_base")
            if ex_id is None:
                continue
            reps = entry.get("reps") or 0
            weight = _safe_float(entry.get("weight"))
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
            return err(exc)
        sessions: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"sets": 0, "reps": 0, "volume_kg": 0.0, "top_weight": 0.0, "entries": []}
        )
        for entry in logs:
            weight = _safe_float(entry.get("weight"))
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
            return err(exc)

        per_ex: dict[int, dict[str, Any]] = {}
        for entry in logs:
            ex_id = entry.get("exercise") or entry.get("exercise_base")
            if ex_id is None:
                continue
            weight = _safe_float(entry.get("weight"))
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
            return bad_request("bucket must be day|week|month")
        if group_by not in GROUP_BY_OPTIONS:
            return bad_request(f"group_by must be one of {list(GROUP_BY_OPTIONS)}")
        selected = _select_metrics(metrics)
        since = date.today() - timedelta(days=days - 1)
        params: dict[str, Any] = {"date__gte": since.isoformat(), "ordering": "date"}
        if exercise_id is not None:
            params["exercise"] = exercise_id
        try:
            logs = await client.paginate("workoutlog/", params=params, limit=5000)
        except WgerError as exc:
            return err(exc)

        ex_cache = await _load_ex_meta(client, logs, group_by)
        buckets: dict[tuple, dict[str, float]] = defaultdict(_new_metric_bucket)
        for entry in logs:
            ex_id = entry.get("exercise") or entry.get("exercise_base")
            d_str = entry.get("date")
            if ex_id is None or not d_str:
                continue
            weight = _safe_float(entry.get("weight"))
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
        if group_by not in GROUP_BY_OPTIONS:
            return bad_request(f"group_by must be one of {list(GROUP_BY_OPTIONS)}")
        selected = _select_metrics(metrics)

        today = date.today()
        a_to = today
        a_from = today - timedelta(days=window_days - 1)
        b_to = a_from - timedelta(days=1 + gap_days)
        b_from = b_to - timedelta(days=window_days - 1)

        # Two range queries instead of one spanning the gap — when gap_days
        # is non-trivial we'd otherwise fetch (and discard) the gap window.
        try:
            logs_a, logs_b = await asyncio.gather(
                client.paginate(
                    "workoutlog/",
                    params={
                        "date__gte": a_from.isoformat(),
                        "date__lte": a_to.isoformat(),
                        "ordering": "date",
                    },
                    limit=5000,
                ),
                client.paginate(
                    "workoutlog/",
                    params={
                        "date__gte": b_from.isoformat(),
                        "date__lte": b_to.isoformat(),
                        "ordering": "date",
                    },
                    limit=5000,
                ),
            )
        except WgerError as exc:
            return err(exc)

        ex_cache = await _load_ex_meta(client, logs_a + logs_b, group_by)
        per_period: dict[str, dict[tuple | None, dict[str, float]]] = {
            "a": defaultdict(_new_metric_bucket),
            "b": defaultdict(_new_metric_bucket),
        }
        totals: dict[str, dict[str, float]] = {
            "a": _new_metric_bucket(),
            "b": _new_metric_bucket(),
        }
        for period, logs in (("a", logs_a), ("b", logs_b)):
            for entry in logs:
                ex_id = entry.get("exercise") or entry.get("exercise_base")
                if ex_id is None:
                    continue
                weight = _safe_float(entry.get("weight"))
                reps = entry.get("reps") or 0
                _accumulate(totals[period], reps, weight)
                for group in _groups_for(ex_id, group_by, ex_cache):
                    _accumulate(per_period[period][group], reps, weight)

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
                out[full] = None if base == 0 else round(((a[full] - base) / base) * 100, 1)
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
