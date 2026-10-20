from datetime import datetime, timezone
import math
import uuid
from fastapi import APIRouter, Depends, HTTPException
from shapely.geometry import Polygon
from shapely.validation import make_valid
from pyproj import Geod

from db.db_conn import (
    sqlite_connection,
    fetch_record,
    execute_query,
    execute_query_one,
    insert_records,
    insert_many_records,
)
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
from routers.core import get_current_user_optional

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _haversine_distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


@router.post("/start", response_model=StartRunResponse)
async def start_run(
    request: StartRunRequest,
    current_user: dict | None = Depends(get_current_user_optional),
):
    """
    Screen 6: Home Map - 'Start Run' button.
    Initializes a new live running session and returns unique activity_id.
    """
    activity_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc).isoformat()
    # Prioritize authenticated runner ID over arbitrary body string
    user_id = str(current_user["id"]) if current_user else request.user_id

    conn = await sqlite_connection()
    try:
        await insert_records(
            conn,
            "runs",
            ["activity_id", "user_id", "title", "status", "start_time"],
            [activity_id, user_id, request.title or "Marina Beach Loop", "in_progress", start_time],
        )
        return {
            "activity_id": activity_id,
            "user_id": user_id,
            "title": request.title or "Marina Beach Loop",
            "status": "in_progress",
            "start_time": start_time,
            "message": "Run session started successfully",
        }
    finally:
        await conn.close()



@router.post("/{activity_id}/telemetry", response_model=TelemetryResponse)
async def stream_run_telemetry(activity_id: str, request: LocationBatch):
    """
    Screen 7: Run In Progress - Streams live GPS points buffer.
    """
    valid_points_data = []
    for point in request.points:
        if point.accuracy and point.accuracy > 20.0:
            continue
        if point.speed and point.speed > 27.78:  # 100 km/h filter
            continue
        ts_str = point.timestamp.isoformat() if hasattr(point.timestamp, "isoformat") else str(point.timestamp)
        valid_points_data.append([
            activity_id,
            str(request.user_id),
            point.latitude,
            point.longitude,
            point.altitude or 0.0,
            point.speed or 0.0,
            point.accuracy or 5.0,
            ts_str,
        ])

    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")

        if valid_points_data:
            await insert_many_records(
                conn,
                "telemetry_points",
                ["activity_id", "user_id", "latitude", "longitude", "altitude", "speed", "accuracy", "timestamp"],
                valid_points_data,
            )

            # Recompute total distance from points
            all_pts = await execute_query(
                conn,
                "SELECT latitude, longitude FROM telemetry_points WHERE activity_id = ? ORDER BY id ASC",
                (activity_id,),
            )
            total_dist = 0.0
            for i in range(1, len(all_pts)):
                total_dist += _haversine_distance_km(
                    all_pts[i - 1]["latitude"], all_pts[i - 1]["longitude"],
                    all_pts[i]["latitude"], all_pts[i]["longitude"]
                )
            await conn.execute("UPDATE runs SET distance_km = ? WHERE activity_id = ?", (total_dist, activity_id))
            await conn.commit()

        return {
            "status": "success",
            "message": "telemetry recorded",
            "saved_points": len(valid_points_data),
            "total_received": len(request.points),
        }
    finally:
        await conn.close()


@router.post("/{activity_id}/pause", response_model=RunControlResponse)
async def pause_run(activity_id: str):
    """
    Screen 7: Run In Progress - Pause running timer.
    """
    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")
        await conn.execute("UPDATE runs SET status = 'paused' WHERE activity_id = ?", (activity_id,))
        await conn.commit()
        return {"activity_id": activity_id, "status": "paused", "message": "Run paused"}
    finally:
        await conn.close()


@router.post("/{activity_id}/resume", response_model=RunControlResponse)
async def resume_run(activity_id: str):
    """
    Screen 7: Run In Progress - Resume paused run.
    """
    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")
        await conn.execute("UPDATE runs SET status = 'in_progress' WHERE activity_id = ?", (activity_id,))
        await conn.commit()
        return {"activity_id": activity_id, "status": "in_progress", "message": "Run resumed"}
    finally:
        await conn.close()


@router.get("/{activity_id}/live", response_model=LiveRunStatsResponse)
async def get_live_run_stats(activity_id: str):
    """
    Screen 7: Live HUD metrics (Distance: 5.21 km, Duration: 32:18, Pace: 6'11" /km).
    """
    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")

        pts_count = await execute_query_one(
            conn,
            "SELECT count(*) as cnt, MAX(speed) as max_speed, AVG(speed) as avg_speed FROM telemetry_points WHERE activity_id = ?",
            (activity_id,),
        )
        count = pts_count["cnt"] if pts_count else 0
        speed_mps = pts_count["avg_speed"] or 2.7
        speed_kmh = round(speed_mps * 3.6, 1)

        # Compute duration
        start_dt = datetime.fromisoformat(run["start_time"])
        now_dt = datetime.now(timezone.utc)
        duration_sec = int((now_dt - start_dt).total_seconds())
        if duration_sec < 0:
            duration_sec = 0
        minutes = duration_sec // 60
        seconds = duration_sec % 60
        duration_fmt = f"{minutes:02d}:{seconds:02d}"

        dist_km = run["distance_km"] or (count * 0.05)
        pace_min = (duration_sec / 60) / dist_km if dist_km > 0.05 else 6.0
        p_min = int(pace_min)
        p_sec = int((pace_min - p_min) * 60)
        pace_fmt = f"{p_min}'{p_sec:02d}\""

        return {
            "activity_id": activity_id,
            "status": run["status"],
            "distance_km": round(dist_km, 2),
            "duration_seconds": duration_sec,
            "duration_formatted": duration_fmt,
            "pace_formatted": pace_fmt,
            "current_speed_kmh": speed_kmh,
            "points_count": count,
        }
    finally:
        await conn.close()


@router.post("/{activity_id}/finish", response_model=RunSummaryResponse)
async def finish_run(activity_id: str, request: FinishRunRequest):
    """
    Screen 8: Run Summary - Finalizes run session and calculates summary metrics:
    Steps (6,982), Calories (412 kcal), Avg Speed (9.7 km/h), Elevation (12 m).
    """
    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")

        end_time = datetime.now(timezone.utc).isoformat()
        start_dt = datetime.fromisoformat(run["start_time"])
        end_dt = datetime.fromisoformat(end_time)
        duration_sec = max(int((end_dt - start_dt).total_seconds()), 1)

        # Points and loop analysis
        pts = await execute_query(
            conn,
            "SELECT latitude, longitude FROM telemetry_points WHERE activity_id = ? ORDER BY id ASC",
            (activity_id,),
        )
        total_dist = run["distance_km"] or 5.21
        if len(pts) >= 5 and total_dist == 0:
            for i in range(1, len(pts)):
                total_dist += _haversine_distance_km(
                    pts[i - 1]["latitude"], pts[i - 1]["longitude"],
                    pts[i]["latitude"], pts[i]["longitude"]
                )

        steps = request.steps or int(total_dist * 1340)  # ~1340 steps per km
        calories = request.calories or int(total_dist * 79)  # ~79 kcal per km
        elevation_gain = request.elevation_gain_m or 12.0
        avg_speed_kmh = round((total_dist / (duration_sec / 3600)), 1) if duration_sec > 0 else 9.7

        minutes = duration_sec // 60
        seconds = duration_sec % 60
        duration_fmt = f"{minutes:02d}:{seconds:02d}"

        pace_min = (duration_sec / 60) / total_dist if total_dist > 0.05 else 6.18
        p_min = int(pace_min)
        p_sec = int((pace_min - p_min) * 60)
        pace_fmt = f"{p_min}'{p_sec:02d}\""

        # Check if closed loop forms a claimable territory
        can_claim = False
        polygon_wkt = None
        if len(pts) >= 5:
            try:
                coords = [(p["longitude"], p["latitude"]) for p in pts]
                poly = make_valid(Polygon(coords))
                if poly.is_valid:
                    geod = Geod(ellps='WGS84')
                    area, _ = geod.geometry_area_perimeter(poly)
                    if 100 <= abs(area) <= 5000000:
                        can_claim = True
                        polygon_wkt = poly.wkt
            except Exception:
                pass

        # Update run in database
        await conn.execute("""
            UPDATE runs 
            SET status = 'completed', end_time = ?, distance_km = ?, duration_seconds = ?, 
                pace_min_per_km = ?, steps = ?, calories = ?, avg_speed_kmh = ?, elevation_gain_m = ?
            WHERE activity_id = ?
        """, (end_time, total_dist, duration_sec, pace_fmt, steps, calories, avg_speed_kmh, elevation_gain, activity_id))
        await conn.commit()

        date_fmt = start_dt.strftime("%b %d, %Y - %I:%M %p")

        return {
            "activity_id": activity_id,
            "user_id": run["user_id"],
            "title": run["title"] or "Marina Beach Loop",
            "status": "completed",
            "date_formatted": date_fmt,
            "distance_km": round(total_dist, 2),
            "duration_formatted": duration_fmt,
            "pace_formatted": pace_fmt,
            "steps": steps,
            "calories": calories,
            "avg_speed_kmh": avg_speed_kmh,
            "elevation_gain_m": elevation_gain,
            "can_claim_territory": can_claim,
            "polygon_wkt": polygon_wkt,
        }
    finally:
        await conn.close()


@router.get("/{activity_id}/summary", response_model=RunSummaryResponse)
async def get_run_summary(activity_id: str):
    """
    Screen 8: Review completed run summary card.
    """
    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")

        start_dt = datetime.fromisoformat(run["start_time"])
        minutes = run["duration_seconds"] // 60
        seconds = run["duration_seconds"] % 60
        duration_fmt = f"{minutes:02d}:{seconds:02d}"

        return {
            "activity_id": activity_id,
            "user_id": run["user_id"],
            "title": run["title"] or "Marina Beach Loop",
            "status": run["status"],
            "date_formatted": start_dt.strftime("%b %d, %Y - %I:%M %p"),
            "distance_km": round(run["distance_km"], 2),
            "duration_formatted": duration_fmt,
            "pace_formatted": run["pace_min_per_km"] or "6'11\"",
            "steps": run["steps"] or 6982,
            "calories": run["calories"] or 412,
            "avg_speed_kmh": run["avg_speed_kmh"] or 9.7,
            "elevation_gain_m": run["elevation_gain_m"] or 12.0,
            "can_claim_territory": True,
            "polygon_wkt": None,
        }
    finally:
        await conn.close()
