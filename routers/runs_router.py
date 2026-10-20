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
from routers.core import get_current_user, get_activity_with_ownership

router = APIRouter(prefix="/runs", tags=["runs"])


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
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 6: Home Map - 'Start Run' button.
    Initializes a new live running session and returns unique activity_id.
    """
    activity_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc).isoformat()
    # Identity taken strictly from token
    user_id = str(current_user["id"])

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
async def stream_run_telemetry(
    activity_id: str,
    request: LocationBatch,
    current_user: dict = Depends(get_current_user),
):
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
            str(current_user["id"]),
            point.latitude,
            point.longitude,
            point.altitude or 0.0,
            point.speed or 0.0,
            point.accuracy or 5.0,
            ts_str,
        ])

    conn = await sqlite_connection()
    try:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])

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
            
            latest_pt = valid_points_data[-1]
            await conn.execute("""
                UPDATE runs 
                SET distance_km = ?, current_latitude = ?, current_longitude = ?, last_telemetry_at = ?
                WHERE activity_id = ?
            """, (total_dist, latest_pt[2], latest_pt[3], latest_pt[7], activity_id))
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
async def pause_run(
    activity_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 7: Run In Progress - Pause running timer.
    """
    conn = await sqlite_connection()
    try:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])
        await conn.execute("UPDATE runs SET status = 'paused' WHERE activity_id = ?", (activity_id,))
        await conn.commit()
        return {"activity_id": activity_id, "status": "paused", "message": "Run paused"}
    finally:
        await conn.close()


@router.post("/{activity_id}/resume", response_model=RunControlResponse)
async def resume_run(
    activity_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 7: Run In Progress - Resume paused run.
    """
    conn = await sqlite_connection()
    try:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])
        await conn.execute("UPDATE runs SET status = 'in_progress' WHERE activity_id = ?", (activity_id,))
        await conn.commit()
        return {"activity_id": activity_id, "status": "in_progress", "message": "Run resumed"}
    finally:
        await conn.close()


@router.get("/{activity_id}/live", response_model=LiveRunStatsResponse)
async def get_live_run_stats(activity_id: str):
    """
    Screen 7: Live HUD metrics and real-time runner coordinates for map tracking.
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
        speed_mps = pts_count["avg_speed"] or 0.0
        speed_kmh = round(speed_mps * 3.6, 1)

        # Compute duration
        start_dt = datetime.fromisoformat(run["start_time"])
        now_dt = datetime.now(timezone.utc)
        duration_sec = max(int((now_dt - start_dt).total_seconds()), 0)
        minutes = duration_sec // 60
        seconds = duration_sec % 60
        duration_fmt = f"{minutes:02d}:{seconds:02d}"

        dist_km = run["distance_km"] or 0.0
        if dist_km > 0.05 and duration_sec > 0:
            pace_min = (duration_sec / 60) / dist_km
            p_min = int(pace_min)
            p_sec = int((pace_min - p_min) * 60)
            pace_fmt = f"{p_min}'{p_sec:02d}\""
        else:
            pace_fmt = "0'00\""

        # Latest GPS point and recent path for live map
        last_pt = await execute_query_one(
            conn,
            "SELECT latitude, longitude, speed FROM telemetry_points WHERE activity_id = ? ORDER BY id DESC LIMIT 1",
            (activity_id,),
        )
        current_lat = last_pt["latitude"] if last_pt else run["current_latitude"]
        current_lng = last_pt["longitude"] if last_pt else run["current_longitude"]
        if last_pt and last_pt["speed"] is not None and last_pt["speed"] > 0:
            speed_kmh = round(last_pt["speed"] * 3.6, 1)

        recent_rows = await execute_query(
            conn,
            "SELECT latitude, longitude, speed, timestamp FROM telemetry_points WHERE activity_id = ? ORDER BY id DESC LIMIT 50",
            (activity_id,),
        )
        recent_pts = [
            {"latitude": r["latitude"], "longitude": r["longitude"], "speed": r["speed"], "timestamp": r["timestamp"]}
            for r in reversed(recent_rows)
        ]

        return {
            "activity_id": activity_id,
            "status": run["status"],
            "distance_km": round(dist_km, 2),
            "duration_seconds": duration_sec,
            "duration_formatted": duration_fmt,
            "pace_formatted": pace_fmt,
            "current_speed_kmh": speed_kmh,
            "points_count": count,
            "current_latitude": current_lat,
            "current_longitude": current_lng,
            "recent_points": recent_pts,
        }
    finally:
        await conn.close()


@router.get("/{activity_id}/path")
async def get_run_full_path(activity_id: str):
    """
    Live breadcrumb path: Returns complete ordered GPS trail for map rendering.
    """
    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")
        pts = await execute_query(
            conn,
            "SELECT latitude, longitude, altitude, speed, timestamp FROM telemetry_points WHERE activity_id = ? ORDER BY id ASC",
            (activity_id,),
        )
        return {
            "activity_id": activity_id,
            "total_points": len(pts),
            "path": [
                {
                    "latitude": p["latitude"],
                    "longitude": p["longitude"],
                    "altitude": p["altitude"],
                    "speed": p["speed"],
                    "timestamp": p["timestamp"],
                }
                for p in pts
            ]
        }
    finally:
        await conn.close()


@router.post("/{activity_id}/finish", response_model=RunSummaryResponse)
async def finish_run(
    activity_id: str,
    request: FinishRunRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 8: Run Summary - Finalizes run session and calculates summary metrics.
    """
    conn = await sqlite_connection()
    try:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])

        end_time = datetime.now(timezone.utc).isoformat()
        start_dt = datetime.fromisoformat(run["start_time"])
        end_dt = datetime.fromisoformat(end_time)
        duration_sec = max(int((end_dt - start_dt).total_seconds()), 1)

        # Points and loop analysis from DB
        pts = await execute_query(
            conn,
            "SELECT latitude, longitude, altitude FROM telemetry_points WHERE activity_id = ? ORDER BY id ASC",
            (activity_id,),
        )
        total_dist = run["distance_km"] or 0.0
        if len(pts) >= 2 and total_dist == 0:
            for i in range(1, len(pts)):
                total_dist += _haversine_distance_km(
                    pts[i - 1]["latitude"], pts[i - 1]["longitude"],
                    pts[i]["latitude"], pts[i]["longitude"]
                )
        elif total_dist == 0 and request.steps:
            # If runner finished with device steps (e.g. smartwatch/treadmill), derive distance from steps
            total_dist = round(request.steps / 1340, 2)

        steps = request.steps if request.steps is not None else int(total_dist * 1340)
        calories = request.calories if request.calories is not None else int(total_dist * 75)
        
        # Elevation gain from telemetry altitudes
        elevation_gain = request.elevation_gain_m
        if elevation_gain is None:
            gain = 0.0
            for i in range(1, len(pts)):
                alt_prev = pts[i - 1]["altitude"] or 0.0
                alt_curr = pts[i]["altitude"] or 0.0
                if alt_curr > alt_prev:
                    gain += (alt_curr - alt_prev)
            elevation_gain = round(gain, 1) if gain > 0 else 0.0

        if total_dist > 0 and duration_sec >= 10:
            avg_speed_kmh = round((total_dist / (duration_sec / 3600)), 1)
        elif total_dist > 0:
            avg_speed_kmh = round(min(total_dist * 2.0, 9.7), 1)
        else:
            avg_speed_kmh = 0.0

        minutes = duration_sec // 60
        seconds = duration_sec % 60
        duration_fmt = f"{minutes:02d}:{seconds:02d}"

        if total_dist > 0.05 and duration_sec > 0:
            pace_min = (duration_sec / 60) / total_dist
            p_min = int(pace_min)
            p_sec = int((pace_min - p_min) * 60)
            pace_fmt = f"{p_min}'{p_sec:02d}\""
        else:
            pace_fmt = "0'00\""

        # Check if closed loop forms a claimable territory
        can_claim = False
        polygon_wkt = None
        if len(pts) >= 3:
            try:
                coords = [(p["longitude"], p["latitude"]) for p in pts]
                # If loop is almost closed or closed
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                poly = make_valid(Polygon(coords))
                if poly.is_valid and not poly.is_empty:
                    geod = Geod(ellps='WGS84')
                    area, _ = geod.geometry_area_perimeter(poly)
                    if abs(area) >= 100:
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
            "title": run["title"] or "City Run",
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
    Screen 8: Review completed run summary card from DB.
    """
    conn = await sqlite_connection()
    try:
        run = await fetch_record(conn, "runs", "activity_id", (activity_id,))
        if not run:
            raise HTTPException(status_code=404, detail="Run activity not found")

        start_dt = datetime.fromisoformat(run["start_time"])
        duration_sec = run["duration_seconds"] or 0
        minutes = duration_sec // 60
        seconds = duration_sec % 60
        duration_fmt = f"{minutes:02d}:{seconds:02d}"

        return {
            "activity_id": activity_id,
            "user_id": run["user_id"],
            "title": run["title"] or "City Run",
            "status": run["status"],
            "date_formatted": start_dt.strftime("%b %d, %Y - %I:%M %p"),
            "distance_km": round(run["distance_km"] or 0.0, 2),
            "duration_formatted": duration_fmt,
            "pace_formatted": run["pace_min_per_km"] or "0'00\"",
            "steps": run["steps"] or 0,
            "calories": run["calories"] or 0,
            "avg_speed_kmh": run["avg_speed_kmh"] or 0.0,
            "elevation_gain_m": run["elevation_gain_m"] or 0.0,
            "can_claim_territory": not bool(run["is_claimed"]),
            "polygon_wkt": None,
        }
    finally:
        await conn.close()
