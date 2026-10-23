"""
Run lifecycle: creation (incl. offline recovery), telemetry ingestion,
pause/resume bookkeeping and authoritative finalisation.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

import asyncpg
from fastapi import HTTPException

from db.database import transaction
from models.datamodel import FinishRunRequest, LocationBatch, LocationPoint
from services import achievements
from services.ownership import get_activity_with_ownership, verify_ownership
from utils.geo import LOOP_MAX_GAP_M, LOOP_MIN_DISTANCE_KM, check_loop, haversine_km, path_length_km
from utils.helper import as_utc, format_duration, format_pace, iso, pace_seconds_per_km, utcnow

MAX_ACCURACY_M = 30.0          # drop fixes with a worse accuracy radius
MAX_SPEED_MPS = 27.78          # ~100 km/h: not running
MAX_AVG_SPEED_MPS = 12.0       # faster than any human average -> clamp duration
STEPS_PER_KM = 1340
KCAL_PER_KM = 75
ACTIVE_RUNNER_WINDOW = timedelta(minutes=10)
DEFAULT_RUN_TITLE = "City Run"

RECORDING, PAUSED, COMPLETED = "recording", "paused", "completed"


def filter_points(points: Optional[Sequence[LocationPoint]]) -> List[LocationPoint]:
    """Drops inaccurate / implausible fixes and orders the rest by device time."""
    kept = [
        p for p in (points or [])
        if (p.accuracy is None or p.accuracy <= MAX_ACCURACY_M)
        and (p.speed is None or p.speed <= MAX_SPEED_MPS)
    ]
    return sorted(kept, key=lambda p: as_utc(p.timestamp))


async def get_or_create_run(
    conn: asyncpg.Connection,
    user: dict,
    activity_id: str,
    *,
    title: Optional[str] = None,
    start_time: Optional[datetime] = None,
) -> Tuple[dict, bool]:
    """
    Loads (and row-locks) a run owned by the caller. Unknown activity ids are
    created on the fly so runs started offline / on a cold serverless
    instance can still be synced. Must be called inside a transaction.
    """
    row = await conn.fetchrow("SELECT * FROM runs WHERE activity_id = $1 FOR UPDATE", activity_id)
    if row:
        verify_ownership(row["user_id"], user["id"])
        return dict(row), False

    row = await conn.fetchrow(
        """
        INSERT INTO runs (activity_id, user_id, title, status, start_time, is_recovered)
        VALUES ($1, $2, $3, 'recording', $4, TRUE)
        ON CONFLICT (activity_id) DO NOTHING
        RETURNING *
        """,
        activity_id, user["id"], title or DEFAULT_RUN_TITLE, start_time or utcnow(),
    )
    if row is not None:
        return dict(row), True
    # Created concurrently by another request
    row = await conn.fetchrow("SELECT * FROM runs WHERE activity_id = $1 FOR UPDATE", activity_id)
    verify_ownership(row["user_id"], user["id"])
    return dict(row), False


async def _last_point(conn: asyncpg.Connection, run_id: int):
    return await conn.fetchrow(
        "SELECT latitude, longitude, recorded_at FROM telemetry_points WHERE run_id = $1 ORDER BY id DESC LIMIT 1",
        run_id,
    )


async def insert_points(conn: asyncpg.Connection, run: dict, points: Sequence[LocationPoint]) -> int:
    """
    Appends already-filtered points that are newer than the last stored fix
    (so retried or overlapping batches are ignored) and advances the
    running distance accumulator. Returns the number of inserted points.
    """
    if not points:
        return 0
    last = await _last_point(conn, run["id"])
    if last is not None:
        points = [p for p in points if as_utc(p.timestamp) > last["recorded_at"]]
        if not points:
            return 0

    added_km = 0.0
    prev = (last["latitude"], last["longitude"]) if last else None
    for p in points:
        if prev is not None:
            added_km += haversine_km(prev[0], prev[1], p.latitude, p.longitude)
        prev = (p.latitude, p.longitude)

    await conn.executemany(
        """
        INSERT INTO telemetry_points (run_id, latitude, longitude, altitude, speed, accuracy, recorded_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        [(run["id"], p.latitude, p.longitude, p.altitude, p.speed, p.accuracy, as_utc(p.timestamp)) for p in points],
    )
    latest = points[-1]
    updated = await conn.fetchrow(
        """
        UPDATE runs
        SET distance_km = distance_km + $2, current_latitude = $3, current_longitude = $4, last_telemetry_at = $5
        WHERE id = $1
        RETURNING distance_km, current_latitude, current_longitude, last_telemetry_at
        """,
        run["id"], added_km, latest.latitude, latest.longitude, utcnow(),
    )
    run.update(dict(updated))
    return len(points)


async def _quick_loop_closed(conn: asyncpg.Connection, run: dict) -> bool:
    """Cheap per-batch loop check: >= 300 m travelled and back within 50 m of the start."""
    if (run["distance_km"] or 0) < LOOP_MIN_DISTANCE_KM:
        return False
    first = await conn.fetchrow(
        "SELECT latitude, longitude FROM telemetry_points WHERE run_id = $1 ORDER BY id LIMIT 1", run["id"]
    )
    if first is None or run["current_latitude"] is None:
        return False
    gap_m = haversine_km(first["latitude"], first["longitude"], run["current_latitude"], run["current_longitude"]) * 1000
    return gap_m <= LOOP_MAX_GAP_M


async def ingest_telemetry(user: dict, activity_id: str, batch: LocationBatch) -> dict:
    points = filter_points(batch.points)
    async with transaction() as conn:
        first_ts = as_utc(points[0].timestamp) if points else None
        run, _ = await get_or_create_run(conn, user, activity_id, start_time=first_ts)
        if run["status"] == COMPLETED:
            raise HTTPException(status_code=409, detail="Run is already completed")
        inserted = await insert_points(conn, run, points)
        if batch.steps is not None:
            await conn.execute("UPDATE runs SET steps = GREATEST(steps, $2) WHERE id = $1", run["id"], batch.steps)
        is_loop_closed = await _quick_loop_closed(conn, run)

    return {
        "status": "success",
        "message": "telemetry recorded",
        "activity_id": activity_id,
        "points_ingested": inserted,
        "saved_points": inserted,
        "total_received": len(batch.points),
        "total_distance_km": round(run["distance_km"] or 0.0, 3),
        "is_loop_closed": is_loop_closed,
    }


def paused_seconds(run: dict, now: datetime) -> int:
    total = run["total_paused_seconds"] or 0
    if run["status"] == PAUSED and run["paused_at"] is not None:
        total += max(int((now - run["paused_at"]).total_seconds()), 0)
    return total


def active_seconds(run: dict, now: datetime) -> int:
    if run["status"] == COMPLETED:
        return run["duration_seconds"] or 0
    elapsed = int((now - run["start_time"]).total_seconds())
    return max(elapsed - paused_seconds(run, now), 0)


async def pause_run(user: dict, activity_id: str) -> dict:
    async with transaction() as conn:
        run = await get_activity_with_ownership(conn, activity_id, user["id"], for_update=True)
        if run["status"] == COMPLETED:
            raise HTTPException(status_code=409, detail="Run is already completed")
        if run["status"] == RECORDING:
            await conn.execute("UPDATE runs SET status = 'paused', paused_at = $2 WHERE id = $1", run["id"], utcnow())
    return {"activity_id": activity_id, "status": PAUSED, "message": "Run paused"}


async def resume_run(user: dict, activity_id: str) -> dict:
    async with transaction() as conn:
        run = await get_activity_with_ownership(conn, activity_id, user["id"], for_update=True)
        if run["status"] == COMPLETED:
            raise HTTPException(status_code=409, detail="Run is already completed")
        if run["status"] == PAUSED:
            await conn.execute(
                "UPDATE runs SET status = 'recording', paused_at = NULL, total_paused_seconds = $2 WHERE id = $1",
                run["id"], paused_seconds(run, utcnow()),
            )
    return {"activity_id": activity_id, "status": RECORDING, "message": "Run resumed"}


async def load_points(conn: asyncpg.Connection, run_id: int):
    return await conn.fetch(
        "SELECT latitude, longitude, altitude, speed, recorded_at FROM telemetry_points WHERE run_id = $1 ORDER BY id",
        run_id,
    )


def _elevation_gain(points) -> float:
    gain, prev = 0.0, None
    for p in points:
        alt = p["altitude"]
        if alt is None:
            continue
        if prev is not None and alt > prev:
            gain += alt - prev
        prev = alt
    return round(gain, 1)


async def recompute_geometry(conn: asyncpg.Connection, run: dict, fallback_distance_km: Optional[float] = None) -> dict:
    """
    Recomputes distance and loop closure from all stored points and persists them.
    """
    points = await load_points(conn, run["id"])
    latlngs = [(p["latitude"], p["longitude"]) for p in points]
    if len(latlngs) >= 2:
        distance_km = path_length_km(latlngs)
    else:
        distance_km = run["distance_km"] or fallback_distance_km or 0.0
    loop = check_loop(latlngs, distance_km)
    duration = run["duration_seconds"] or 0
    if run["status"] == COMPLETED and distance_km > 0:
        duration = max(duration, int(distance_km * 1000 / MAX_AVG_SPEED_MPS))
    row = await conn.fetchrow(
        """
        UPDATE runs
        SET distance_km = $2, is_loop_closed = $3, loop_area_km2 = $4, loop_polygon_wkt = $5,
            duration_seconds = $6, avg_pace_seconds = $7, avg_speed_kmh = $8
        WHERE id = $1
        RETURNING *
        """,
        run["id"],
        distance_km,
        loop.is_closed,
        loop.polygon.area_km2 if loop.is_closed else 0.0,
        loop.polygon.wkt if loop.is_closed else None,
        duration,
        pace_seconds_per_km(duration, distance_km),
        round(distance_km / (duration / 3600), 1) if duration else 0.0,
    )
    run.update(dict(row))
    return run


@dataclass
class FinishResult:
    run: dict
    badges: List[str] = field(default_factory=list)


async def finalize_run(conn: asyncpg.Connection, user: dict, run: dict, request: FinishRunRequest) -> FinishResult:
    """
    Freezes a run: ingests client breadcrumbs the server is missing, computes
    duration (minus pauses), distance, pace, elevation and loop closure.
    Finishing an already completed run is idempotent.
    """
    if run["status"] == COMPLETED:
        return FinishResult(run)

    now = utcnow()
    await insert_points(conn, run, filter_points(request.points))
    points = await load_points(conn, run["id"])
    latlngs = [(p["latitude"], p["longitude"]) for p in points]

    if len(latlngs) >= 2:
        distance_km = path_length_km(latlngs)
    elif request.distance_km:
        distance_km = request.distance_km
    elif request.steps:
        distance_km = request.steps / STEPS_PER_KM
    else:
        distance_km = run["distance_km"] or 0.0

    if run["is_recovered"]:
        # Started offline: the server never saw the start, trust the device clock
        span = int((points[-1]["recorded_at"] - points[0]["recorded_at"]).total_seconds()) if len(points) >= 2 else 0
        duration = request.active_seconds or span or active_seconds(run, now)
    else:
        duration = active_seconds(run, now)
    if distance_km > 0:
        duration = max(duration, int(distance_km * 1000 / MAX_AVG_SPEED_MPS))
    duration = max(duration, 1)

    steps = request.steps if request.steps is not None else (run["steps"] or int(distance_km * STEPS_PER_KM))
    calories = request.calories if request.calories is not None else int(distance_km * KCAL_PER_KM)
    elevation = request.elevation_gain_m if request.elevation_gain_m is not None else _elevation_gain(points)
    avg_speed_kmh = round(distance_km / (duration / 3600), 1) if duration else 0.0
    loop = check_loop(latlngs, distance_km)
    end_lat = request.end_latitude if request.end_latitude is not None else run["current_latitude"]
    end_lng = request.end_longitude if request.end_longitude is not None else run["current_longitude"]

    row = await conn.fetchrow(
        """
        UPDATE runs
        SET status = 'completed', end_time = $2, paused_at = NULL, total_paused_seconds = $3,
            distance_km = $4, duration_seconds = $5, avg_pace_seconds = $6, steps = $7, calories = $8,
            avg_speed_kmh = $9, elevation_gain_m = $10, is_loop_closed = $11, loop_area_km2 = $12,
            loop_polygon_wkt = $13, current_latitude = $14, current_longitude = $15
        WHERE id = $1
        RETURNING *
        """,
        run["id"], now, paused_seconds(run, now), distance_km, duration,
        pace_seconds_per_km(duration, distance_km), steps, calories, avg_speed_kmh, elevation,
        loop.is_closed,
        loop.polygon.area_km2 if loop.is_closed else 0.0,
        loop.polygon.wkt if loop.is_closed else None,
        end_lat, end_lng,
    )
    keys = ["first_run"] + (["10k_club"] if distance_km >= 10 else [])
    badges = await achievements.award(conn, user["id"], keys)
    return FinishResult(dict(row), badges)


async def finish_run(user: dict, activity_id: str, request: FinishRunRequest) -> dict:
    points = filter_points(request.points)
    async with transaction() as conn:
        start = as_utc(points[0].timestamp) if points else None
        run, _ = await get_or_create_run(conn, user, activity_id, start_time=start)
        result = await finalize_run(conn, user, run, request)
    return run_summary(result.run, result.badges)


def run_summary(run: dict, badges: Optional[List[str]] = None) -> dict:
    duration = run["duration_seconds"] if run["status"] == COMPLETED else active_seconds(run, utcnow())
    is_loop_closed = bool(run["is_loop_closed"])
    return {
        "activity_id": run["activity_id"],
        "user_id": str(run["user_id"]),
        "title": run["title"] or DEFAULT_RUN_TITLE,
        "status": run["status"],
        "date_formatted": run["start_time"].strftime("%b %d, %Y - %I:%M %p"),
        "start_time": iso(run["start_time"]),
        "end_time": iso(run["end_time"]),
        "distance_km": round(run["distance_km"] or 0.0, 2),
        "duration_seconds": duration,
        "duration_formatted": format_duration(duration),
        "pace_formatted": format_pace(pace_seconds_per_km(duration, run["distance_km"] or 0.0)),
        "steps": run["steps"] or 0,
        "calories": run["calories"] or 0,
        "avg_speed_kmh": run["avg_speed_kmh"] or 0.0,
        "elevation_gain_m": run["elevation_gain_m"] or 0.0,
        "is_loop_closed": is_loop_closed,
        "can_claim_territory": is_loop_closed and run["status"] == COMPLETED and not run["is_claimed"],
        "is_claimed": bool(run["is_claimed"]),
        "suggested_area_km2": run["loop_area_km2"] or 0.0,
        "polygon_wkt": run["loop_polygon_wkt"],
        "badges_earned": badges or [],
    }
