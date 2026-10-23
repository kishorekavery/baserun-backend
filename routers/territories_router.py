from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from db.database import connection
from models.datamodel import (
    ChallengeRequest,
    ChallengeResponse,
    ClaimFromRunRequest,
    HomeMapResponse,
    NearbyTerritoriesResponse,
    TerritoryClaimedResponse,
    TerritoryDetailResponse,
)
from routers.core import get_current_user, get_current_user_optional
from services import territory_service
from services.run_service import ACTIVE_RUNNER_WINDOW
from services.territory_service import DEFAULT_COLOR, TERRITORY_SELECT, effective_defense_level
from utils.geo import bbox_for_radius, distance_to_polygon_m, load_polygon, simplify_for_client
from utils.helper import format_pace, iso, utcnow

router = APIRouter(prefix="/api/v1", tags=["territories"])

MAX_MAP_TERRITORIES = 1000


@router.get("/territories/nearby", response_model=NearbyTerritoriesResponse)
async def get_nearby_territories(
    lat: float = Query(13.0827, ge=-90, le=90),
    lng: float = Query(80.2707, ge=-180, le=180),
    radius_km: float = Query(5.0, gt=0, le=50.0),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Screens 5/6: active territories within `radius_km` of a point, nearest
    first. Distance is 0 when the point is inside the territory.
    """
    min_lat, min_lng, max_lat, max_lng = bbox_for_radius(lat, lng, radius_km)
    async with connection() as conn:
        rows = await conn.fetch(
            TERRITORY_SELECT + """
            WHERE t.status = 'active'
              AND t.max_lat >= $1 AND t.min_lat <= $2 AND t.max_lng >= $3 AND t.min_lng <= $4
            LIMIT $5
            """,
            min_lat, max_lat, min_lng, max_lng, MAX_MAP_TERRITORIES,
        )

    items = []
    for r in rows:
        polygon = load_polygon(r["polygon_wkt"])
        if polygon is None:
            continue
        distance_m = distance_to_polygon_m(lat, lng, polygon)
        if distance_m > radius_km * 1000:
            continue
        color = r["owner_color"] or DEFAULT_COLOR
        items.append({
            "id": r["id"],
            "name": r["name"],
            "owner_id": str(r["owner_id"]) if r["owner_id"] is not None else None,
            "owner_username": r["owner_username"] or "",
            "owner_color": color,
            "color": color,
            "area_km2": r["area_km2"],
            "distance_meters": round(distance_m, 1),
            "distance_km": round(distance_m / 1000, 2),
        })
    items.sort(key=lambda item: item["distance_meters"])
    items = items[:limit]
    return {
        "latitude": lat,
        "longitude": lng,
        "radius_km": radius_km,
        "count": len(items),
        "total_nearby": len(items),
        "territories": items,
    }


@router.get("/map/home", response_model=HomeMapResponse)
async def get_home_map(
    user_id: Optional[int] = Query(None, description="Legacy: viewer id when no Bearer token is sent"),
    lat: float = Query(13.0827, ge=-90, le=90),
    lng: float = Query(80.2707, ge=-180, le=180),
    radius_km: float = Query(10.0, gt=0, le=100.0),
    min_lat: Optional[float] = Query(None, ge=-90, le=90),
    min_lng: Optional[float] = Query(None, ge=-180, le=180),
    max_lat: Optional[float] = Query(None, ge=-90, le=90),
    max_lng: Optional[float] = Query(None, ge=-180, le=180),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Screen 6: territories in the viewport (bbox) or around lat/lng, with
    ownership flags for the caller and HUD counters.
    """
    bbox = (min_lat, min_lng, max_lat, max_lng)
    if any(v is not None for v in bbox):
        if any(v is None for v in bbox):
            raise HTTPException(status_code=422, detail="min_lat, min_lng, max_lat and max_lng must be sent together")
        if min_lat > max_lat or min_lng > max_lng:
            raise HTTPException(status_code=422, detail="Bounding box minimums must not exceed maximums")
    else:
        bbox = bbox_for_radius(lat, lng, radius_km)
    b_min_lat, b_min_lng, b_max_lat, b_max_lng = bbox

    async with connection() as conn:
        viewer_id = current_user["id"] if current_user else user_id
        viewer_club_id = None
        if current_user:
            viewer_club_id = current_user["club_id"]
        elif viewer_id is not None:
            viewer_club_id = await conn.fetchval("SELECT club_id FROM users WHERE id = $1", viewer_id)

        rows = await conn.fetch(
            """
            SELECT t.*,
                   COALESCE(t.owner_id = $6::int, FALSE) AS is_user_territory,
                   COALESCE(t.club_id = $7::int, FALSE) AS is_club_territory,
                   u.username AS owner_username, u.territory_color AS owner_color,
                   c.name AS club_name, c.color AS club_color
            FROM territories t
            LEFT JOIN users u ON u.id = t.owner_id
            LEFT JOIN clubs c ON c.id = t.club_id
            WHERE t.max_lat >= $1 AND t.min_lat <= $2 AND t.max_lng >= $3 AND t.min_lng <= $4
            ORDER BY t.id DESC
            LIMIT $5
            """,
            b_min_lat, b_max_lat, b_min_lng, b_max_lng, MAX_MAP_TERRITORIES, viewer_id, viewer_club_id,
        )
        counters = await conn.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM territories WHERE status = 'active' AND owner_id = $1) AS user_count,
                (SELECT count(*) FROM territories WHERE status = 'active' AND club_id = $2) AS club_count,
                (SELECT count(DISTINCT user_id) FROM runs
                  WHERE status IN ('recording', 'paused') AND last_telemetry_at > $3
                    AND current_latitude BETWEEN $4 AND $5 AND current_longitude BETWEEN $6 AND $7) AS active_runners
            """,
            viewer_id, viewer_club_id, utcnow() - ACTIVE_RUNNER_WINDOW,
            b_min_lat, b_max_lat, b_min_lng, b_max_lng,
        )

    territories = []
    unclaimed = 0
    for r in rows:
        if r["status"] == "unclaimed":
            unclaimed += 1
        territories.append({
            "id": r["id"],
            "name": r["name"],
            "owner_id": str(r["owner_id"]) if r["owner_id"] is not None else None,
            "owner_username": r["owner_username"] or "",
            "owner_color": r["owner_color"] or DEFAULT_COLOR,
            "club_id": r["club_id"],
            "club_name": r["club_name"],
            "club_color": r["club_color"],
            "status": r["status"],
            "area_km2": r["area_km2"],
            "defense_level": effective_defense_level(r),
            "polygon_wkt": simplify_for_client(r["polygon_wkt"], r["area_km2"]),
            "is_user_territory": r["is_user_territory"],
            "is_club_territory": r["is_club_territory"],
        })

    return {
        "active_runners_nearby": counters["active_runners"],
        "user_territories_count": counters["user_count"],
        "club_territories_count": counters["club_count"],
        "unclaimed_territories_count": unclaimed,
        "territories": territories,
    }


@router.post("/territories/claim-from-run", response_model=TerritoryClaimedResponse)
async def claim_territory_from_run(
    request: ClaimFromRunRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 9: turn a closed-loop run into a territory. Area and perimeter
    are computed server-side. A loop that matches an existing base (> 75%
    overlap) fortifies your own base, or takes over a rival's base when
    your pace beats its record.
    """
    return await territory_service.claim_from_run(current_user, request)


@router.get("/territories/{territory_id}", response_model=TerritoryDetailResponse)
async def get_territory_details(
    territory_id: int,
    current_user_id: Optional[int] = Query(None, description="Legacy: viewer id when no Bearer token is sent"),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Screen 10: owner, club, defense stats and record pace of a territory.
    """
    async with connection() as conn:
        row = await conn.fetchrow(TERRITORY_SELECT + " WHERE t.id = $1", territory_id)
    if not row:
        raise HTTPException(status_code=404, detail="Territory not found")

    now = utcnow()
    viewer_id = current_user["id"] if current_user else current_user_id
    is_owner = viewer_id is not None and row["owner_id"] == viewer_id
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "area_km2": row["area_km2"],
        "perimeter_km": row["perimeter_km"],
        "claimed_on": row["conquered_at"].strftime("%b %d, %Y"),
        "held_since": iso(row["conquered_at"]),
        "owner_id": str(row["owner_id"]) if row["owner_id"] is not None else None,
        "owner_username": row["owner_username"] or "",
        "owner_avatar": row["owner_avatar"] or "",
        "owner_color": row["owner_color"] or DEFAULT_COLOR,
        "club_id": row["club_id"],
        "club_name": row["club_name"],
        "club_color": row["club_color"],
        "record_pace": format_pace(row["record_pace_seconds"]),
        "record_pace_seconds": row["record_pace_seconds"],
        "defense_level": effective_defense_level(row, now),
        "times_defended": row["times_defended"],
        "times_challenged": row["times_challenged"],
        "total_runs": row["total_runs"],
        "total_km": round(row["total_km"], 1),
        "days_active": max((now - row["conquered_at"]).days, 1),
        "polygon_wkt": row["polygon_wkt"],
        "is_owner": is_owner,
        "is_user_territory": is_owner,
        "can_challenge": viewer_id is not None and not is_owner,
    }


@router.post("/territories/{territory_id}/challenge", response_model=ChallengeResponse)
async def challenge_territory(
    territory_id: int,
    request: Optional[ChallengeRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 10: register intent to challenge a rival base. Beat its record
    pace on the perimeter and claim the run to take it over.
    """
    async with connection() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(TERRITORY_SELECT + " WHERE t.id = $1", territory_id)
            if not row:
                raise HTTPException(status_code=404, detail="Territory not found")
            if row["owner_id"] == current_user["id"]:
                raise HTTPException(status_code=400, detail="You cannot challenge your own territory")

            challenge = await conn.fetchrow(
                """
                SELECT challenge_id FROM challenges
                WHERE territory_id = $1 AND challenger_id = $2 AND status = 'active'
                """,
                territory_id, current_user["id"],
            )
            if challenge is None:
                challenge = await conn.fetchrow(
                    """
                    INSERT INTO challenges (challenge_id, territory_id, challenger_id, target_pace_seconds)
                    VALUES ($1, $2, $3, $4)
                    RETURNING challenge_id
                    """,
                    territory_service.new_challenge_id(), territory_id, current_user["id"], row["record_pace_seconds"],
                )

    target_pace = format_pace(row["record_pace_seconds"])
    owner = row["owner_username"] or "nobody"
    if row["record_pace_seconds"]:
        instructions = (
            f"Run the {row['perimeter_km']:.2f} km perimeter of {row['name']} faster than {target_pace} /km "
            f"to claim this base from @{owner}!"
        )
    else:
        instructions = f"Run the {row['perimeter_km']:.2f} km perimeter of {row['name']} to claim this base!"
    return {
        "challenge_id": challenge["challenge_id"],
        "territory_id": territory_id,
        "target_territory_id": territory_id,
        "territory_name": row["name"],
        "current_owner": owner,
        "challenger_id": str(current_user["id"]),
        "status": "challenge_active",
        "target_pace": target_pace,
        "target_distance_km": row["perimeter_km"],
        "instructions": instructions,
    }
