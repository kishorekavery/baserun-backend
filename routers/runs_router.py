import secrets

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from db.database import connection
from models.datamodel import (
    FinishRunRequest,
    LiveRunStatsResponse,
    LocationBatch,
    RunControlResponse,
    RunSummaryResponse,
    StartRunRequest,
    StartRunResponse,
    TelemetryResponse,
)
from routers.core import get_current_user, get_activity_with_ownership
from services import run_service
from utils.geo import LOOP_MAX_GAP_M, LOOP_MIN_DISTANCE_KM, haversine_km
from utils.helper import format_duration, format_pace, iso, pace_seconds_per_km, utcnow

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("/start", response_model=StartRunResponse)
async def start_run(
    request: StartRunRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 6/7: 'Start Run'. Creates a recording session and returns its activity_id.
    """
    activity_id = request.activity_id or f"act_{secrets.token_hex(12)}"
    title = (request.title or "").strip() or run_service.DEFAULT_RUN_TITLE
    async with connection() as conn:
        if request.target_territory_id is not None:
            exists = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM territories WHERE id = $1)", request.target_territory_id)
            if not exists:
                raise HTTPException(status_code=404, detail="Target territory not found")
        try:
            run = await conn.fetchrow(
                """
                INSERT INTO runs (
                    activity_id, user_id, title, route_mode, target_territory_id, device_info,
                    start_latitude, start_longitude, current_latitude, current_longitude
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $7, $8)
                RETURNING activity_id, status, start_time
                """,
                activity_id, current_user["id"], title, request.route_mode or "free_run",
                request.target_territory_id, request.device_info,
                request.start_latitude, request.start_longitude,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail="A run with this activity_id already exists")
    return {
        "activity_id": run["activity_id"],
        "user_id": str(current_user["id"]),
        "title": title,
        "status": run["status"],
        "start_time": iso(run["start_time"]),
        "message": "Run session started successfully",
    }


@router.post("/{activity_id}/telemetry", response_model=TelemetryResponse)
async def stream_run_telemetry(
    activity_id: str,
    request: LocationBatch,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 7: periodic GPS batch flush. Fixes worse than 30 m accuracy or
    faster than 100 km/h are dropped; retried batches are de-duplicated.
    """
    return await run_service.ingest_telemetry(current_user, activity_id, request)


@router.post("/{activity_id}/pause", response_model=RunControlResponse)
async def pause_run(activity_id: str, current_user: dict = Depends(get_current_user)):
    """Screen 7: pause the run timer."""
    return await run_service.pause_run(current_user, activity_id)


@router.post("/{activity_id}/resume", response_model=RunControlResponse)
async def resume_run(activity_id: str, current_user: dict = Depends(get_current_user)):
    """Screen 7: resume a paused run."""
    return await run_service.resume_run(current_user, activity_id)


@router.get("/{activity_id}/live", response_model=LiveRunStatsResponse)
async def get_live_run_stats(activity_id: str, current_user: dict = Depends(get_current_user)):
    """
    Screen 7: live HUD metrics and the latest breadcrumbs of the caller's run.
    """
    now = utcnow()
    async with connection() as conn:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])
        count = await conn.fetchval("SELECT count(*) FROM telemetry_points WHERE run_id = $1", run["id"])
        recent = await conn.fetch(
            """
            SELECT latitude, longitude, speed, recorded_at FROM telemetry_points
            WHERE run_id = $1 ORDER BY id DESC LIMIT 50
            """,
            run["id"],
        )
        first = await conn.fetchrow(
            "SELECT latitude, longitude FROM telemetry_points WHERE run_id = $1 ORDER BY id LIMIT 1", run["id"]
        )

    duration = run_service.active_seconds(run, now)
    distance_km = run["distance_km"] or 0.0
    if recent and recent[0]["speed"]:
        speed_kmh = round(recent[0]["speed"] * 3.6, 1)
    else:
        speed_kmh = round(distance_km / (duration / 3600), 1) if duration else 0.0

    is_loop_closed = run["is_loop_closed"]
    if run["status"] != run_service.COMPLETED and first is not None and recent:
        gap_km = haversine_km(first["latitude"], first["longitude"], recent[0]["latitude"], recent[0]["longitude"])
        is_loop_closed = distance_km >= LOOP_MIN_DISTANCE_KM and gap_km * 1000 <= LOOP_MAX_GAP_M

    return {
        "activity_id": activity_id,
        "status": run["status"],
        "distance_km": round(distance_km, 2),
        "duration_seconds": duration,
        "duration_formatted": format_duration(duration),
        "pace_formatted": format_pace(pace_seconds_per_km(duration, distance_km)),
        "current_speed_kmh": speed_kmh,
        "points_count": count,
        "is_loop_closed": bool(is_loop_closed),
        "current_latitude": run["current_latitude"],
        "current_longitude": run["current_longitude"],
        "recent_points": [
            {"latitude": r["latitude"], "longitude": r["longitude"], "speed": r["speed"], "timestamp": iso(r["recorded_at"])}
            for r in reversed(recent)
        ],
    }


@router.get("/{activity_id}/path")
async def get_run_full_path(activity_id: str, current_user: dict = Depends(get_current_user)):
    """
    Complete ordered GPS trail of the caller's run for map rendering.
    """
    async with connection() as conn:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])
        points = await run_service.load_points(conn, run["id"])
    return {
        "activity_id": activity_id,
        "total_points": len(points),
        "path": [
            {
                "latitude": p["latitude"],
                "longitude": p["longitude"],
                "altitude": p["altitude"],
                "speed": p["speed"],
                "timestamp": iso(p["recorded_at"]),
            }
            for p in points
        ],
    }


@router.post("/{activity_id}/finish", response_model=RunSummaryResponse)
async def finish_run(
    activity_id: str,
    request: FinishRunRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 8: finalise the run (idempotent). Unknown activity ids are
    recovered from the client breadcrumbs sent in `points`.
    """
    return await run_service.finish_run(current_user, activity_id, request)


@router.get("/{activity_id}/summary", response_model=RunSummaryResponse)
async def get_run_summary(activity_id: str, current_user: dict = Depends(get_current_user)):
    """
    Screen 8: stored summary of one of the caller's runs.
    """
    async with connection() as conn:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])
    return run_service.run_summary(run)
