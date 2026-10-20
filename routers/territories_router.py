from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from shapely.geometry import Polygon
from shapely.validation import make_valid
from pyproj import Geod

from db.db_conn import (
    sqlite_connection,
    fetch_record,
    execute_query,
    execute_query_one,
    insert_record_get_id,
    insert_records,
)
from models.datamodel import (
    ChallengeRequest,
    ChallengeResponse,
    ClaimFromRunRequest,
    HomeMapResponse,
    HomeMapTerritory,
    NearbyTerritoriesResponse,
    NearbyTerritoryItem,
    TerritoryClaimedResponse,
    TerritoryDetailResponse,
)
from routers.core import get_current_user, get_activity_with_ownership

router = APIRouter(prefix="/api/v1", tags=["territories"])



from shapely import wkt
from routers.runs_router import _haversine_distance_km


@router.get("/territories/nearby", response_model=NearbyTerritoriesResponse)
async def get_nearby_territories(
    lat: float = Query(13.0827, ge=-90, le=90),
    lng: float = Query(80.2707, ge=-180, le=180),
    radius_km: float = Query(5.0, ge=0.5, le=50.0),
):
    """
    Screen 5: Location Permission Screen
    Fetches real nearby territories from DB computed using Haversine distance.
    """
    conn = await sqlite_connection()
    try:
        rows = await execute_query(
            conn,
            """
            SELECT cb.id, cb.name, cb.area, cb.color, cb.polygon_wkt, u.username as owner_username
            FROM claimed_bases cb
            JOIN users u ON cb.user_id = u.id
            WHERE cb.status = 'active'
            """
        )
        items = []
        for r in rows:
            area_km2 = round(r["area"] / 1_000_000, 2)
            c_lat, c_lng = lat, lng
            try:
                geom = wkt.loads(r["polygon_wkt"])
                c_lng, c_lat = geom.centroid.x, geom.centroid.y
            except Exception:
                pass
            dist = round(_haversine_distance_km(lat, lng, c_lat, c_lng), 2)
            if dist <= radius_km or len(items) < 3:
                items.append(NearbyTerritoryItem(
                    id=r["id"],
                    name=r["name"] or f"Territory #{r['id']}",
                    owner_username=r["owner_username"],
                    color=r["color"] or "#00E5FF",
                    area_km2=area_km2,
                    distance_km=dist,
                ))

        items.sort(key=lambda x: x.distance_km)

        return {
            "latitude": lat,
            "longitude": lng,
            "radius_km": radius_km,
            "total_nearby": len(items),
            "territories": items,
        }
    finally:
        await conn.close()


@router.get("/map/home", response_model=HomeMapResponse)
async def get_home_map(
    user_id: str = Query("1", description="Current logged-in user ID"),
    lat: float = Query(13.0827),
    lng: float = Query(80.2707),
):
    """
    Screen 6: Home Map API
    Returns territories classified by ownership from DB:
    - Cyan: Your Territory
    - Magenta: Club Territory
    - Grey: Unclaimed zones
    """
    conn = await sqlite_connection()
    try:
        user = await fetch_record(conn, "users", "id", (int(user_id) if user_id.isdigit() else 1,))
        user_club_id = user["club_id"] if user else None

        active_runners_row = await execute_query_one(
            conn,
            "SELECT count(DISTINCT user_id) as cnt FROM runs WHERE status = 'in_progress'",
        )
        active_runners_nearby = active_runners_row["cnt"] if active_runners_row else 0

        unclaimed_row = await execute_query_one(
            conn,
            "SELECT count(*) as cnt FROM claimed_bases WHERE status = 'unclaimed'",
        )
        unclaimed_count = unclaimed_row["cnt"] if unclaimed_row else 0

        rows = await execute_query(
            conn,
            """
            SELECT cb.*, u.username as owner_username, u.territory_color, c.name as club_name, c.color as club_color
            FROM claimed_bases cb
            LEFT JOIN users u ON cb.user_id = u.id
            LEFT JOIN clubs c ON cb.club_id = c.id
            WHERE cb.status = 'active'
            ORDER BY cb.id DESC
            """
        )

        territories = []
        user_cnt = 0
        club_cnt = 0

        for r in rows:
            is_user = str(r["user_id"]) == str(user_id)
            is_club = (user_club_id is not None and r["club_id"] == user_club_id) and not is_user
            if is_user:
                user_cnt += 1
            elif is_club:
                club_cnt += 1

            area_km2 = round(r["area"] / 1_000_000, 2)
            territories.append(HomeMapTerritory(
                id=r["id"],
                name=r["name"] or f"Territory #{r['id']}",
                owner_id=str(r["user_id"]),
                owner_username=r["owner_username"] or "runner",
                owner_color=r["color"] or "#00E5FF",
                club_name=r["club_name"],
                club_color=r["club_color"] or "#E91E63",
                area_km2=area_km2,
                polygon_wkt=r["polygon_wkt"],
                is_user_territory=is_user,
                is_club_territory=is_club,
            ))

        return {
            "active_runners_nearby": active_runners_nearby,
            "user_territories_count": user_cnt,
            "club_territories_count": club_cnt,
            "unclaimed_territories_count": unclaimed_count,
            "territories": territories,
        }
    finally:
        await conn.close()


@router.post("/territories/claim-from-run", response_model=TerritoryClaimedResponse)
async def claim_territory_from_run(
    request: ClaimFromRunRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 9: Territory Claimed (Success Screen)
    Converts a completed closed-loop run into an official claimed territory in DB.
    Awards celebration payload, badge, and share text.
    """
    conn = await sqlite_connection()
    try:
        run = await get_activity_with_ownership(conn, request.activity_id, current_user["id"])

        if run["is_claimed"] == 1:
            raise HTTPException(status_code=400, detail="This run has already been claimed as a territory")

        pts = await execute_query(
            conn,
            "SELECT latitude, longitude FROM telemetry_points WHERE activity_id = ? ORDER BY id ASC",
            (request.activity_id,),
        )
        if len(pts) < 3:
            raise HTTPException(
                status_code=400,
                detail="At least 3 GPS points forming a closed loop are required to claim territory",
            )

        coords = [(p["longitude"], p["latitude"]) for p in pts]
        if coords[0] != coords[-1]:
            coords.append(coords[0])

        poly = make_valid(Polygon(coords))
        if not poly.is_valid or poly.is_empty:
            poly = make_valid(poly)

        geod = Geod(ellps='WGS84')
        area, perimeter = geod.geometry_area_perimeter(poly)
        area_sqm = abs(area)
        perimeter_m = abs(perimeter)
        area_km2 = round(area_sqm / 1_000_000, 2)

        user = await fetch_record(conn, "users", "id", (int(run["user_id"]) if str(run["user_id"]).isdigit() else 1,))
        owner_username = user["username"] if user else current_user["username"]
        owner_avatar = user["avatar_url"] if user else ""
        owner_color = user["territory_color"] if user else "#00E5FF"
        club_id = user["club_id"] if user else None
        territory_name = request.territory_name or f"Territory #{run['id']}"

        # Save to claimed_bases in DB
        territory_id = await insert_record_get_id(
            conn,
            "claimed_bases",
            ["name", "user_id", "club_id", "status", "area", "perimeter", "polygon_wkt", "color", "total_runs", "total_km"],
            [territory_name, str(run["user_id"]), club_id, "active", area_sqm, perimeter_m, poly.wkt, owner_color, 1, run["distance_km"] or 0.0],
        )

        # Mark run as claimed in DB
        await conn.execute("UPDATE runs SET is_claimed = 1 WHERE activity_id = ?", (request.activity_id,))

        # Update club statistics in DB if user is member of a club
        if club_id:
            await conn.execute("""
                UPDATE club_members 
                SET territories_contributed = territories_contributed + 1, km_contributed = km_contributed + ?
                WHERE club_id = ? AND user_id = ?
            """, (run["distance_km"] or 0.0, club_id, str(run["user_id"])))
            await conn.execute("""
                UPDATE clubs 
                SET territory_count = territory_count + 1, total_km = total_km + ?
                WHERE id = ?
            """, (run["distance_km"] or 0.0, club_id))

        # Unlock celebration badge in DB
        badge_title = f"{territory_name} Conqueror"
        await insert_records(
            conn,
            "achievements",
            ["user_id", "title", "description", "badge_icon"],
            [str(run["user_id"]), badge_title, f"Claimed {area_km2} km² turf at {territory_name}", "crown"],
        )
        await conn.commit()

        share_text = f"I just claimed {territory_name} ({area_km2} km²) on Run to Claim! Can you conquer my turf? #RunToClaim"

        return {
            "status": "success",
            "message": "Territory claimed successfully",
            "territory_id": territory_id,
            "territory_name": territory_name,
            "area_km2": area_km2,
            "perimeter_km": round(perimeter_m / 1000, 2),
            "owner_username": owner_username,
            "owner_avatar": owner_avatar or "",
            "polygon_wkt": poly.wkt,
            "badge_earned": badge_title,
            "share_text": share_text,
        }
    finally:
        await conn.close()


@router.get("/territories/{territory_id}", response_model=TerritoryDetailResponse)
async def get_territory_details(
    territory_id: int,
    current_user_id: str = Query("1", description="Current viewer user ID"),
):
    """
    Screen 10: Territory Details from DB.
    """
    conn = await sqlite_connection()
    try:
        row = await execute_query_one(
            conn,
            """
            SELECT cb.*, u.username as owner_username, u.avatar_url as owner_avatar, u.territory_color
            FROM claimed_bases cb
            LEFT JOIN users u ON cb.user_id = u.id
            WHERE cb.id = ?
            """,
            (territory_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Territory not found")

        area_km2 = round(row["area"] / 1_000_000, 2)
        perimeter_km = round(row["perimeter"] / 1000, 2)
        claimed_dt = datetime.fromisoformat(row["created_at"]) if "T" in str(row["created_at"]) else datetime.now(timezone.utc)
        days_active = max((datetime.now(timezone.utc) - claimed_dt).days, 1)

        return {
            "id": row["id"],
            "name": row["name"] or f"Territory #{row['id']}",
            "area_km2": area_km2,
            "perimeter_km": perimeter_km,
            "claimed_on": claimed_dt.strftime("%b %d, %Y"),
            "owner_id": str(row["user_id"]),
            "owner_username": row["owner_username"] or "runner",
            "owner_avatar": row["owner_avatar"] or "",
            "owner_color": row["color"] or "#00E5FF",
            "total_runs": row["total_runs"] or 1,
            "total_km": round(row["total_km"] or 0.0, 1),
            "days_active": days_active,
            "polygon_wkt": row["polygon_wkt"],
            "is_owner": str(row["user_id"]) == str(current_user_id),
        }
    finally:
        await conn.close()


@router.post("/territories/{territory_id}/challenge", response_model=ChallengeResponse)
async def challenge_territory(
    territory_id: int,
    request: ChallengeRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 10: Challenge Territory Owner
    Initiates a turf war challenge against the current owner.
    """
    conn = await sqlite_connection()
    try:
        t = await fetch_record(conn, "claimed_bases", "id", (territory_id,))
        if not t:
            raise HTTPException(status_code=404, detail="Territory not found")

        owner_user = await fetch_record(conn, "users", "id", (int(t["user_id"]) if str(t["user_id"]).isdigit() else 1,))
        owner_name = owner_user["username"] if owner_user else "adventurer99"

        challenge_id = f"chal_{uuid.uuid4().hex[:8]}"
        t_name = t["name"] or f"Territory #{t['id']}"
        instructions = f"Run around {t_name} and complete the loop to steal this territory from @{owner_name}!"
        challenger_id = str(current_user["id"])

        return {
            "challenge_id": challenge_id,
            "territory_id": territory_id,
            "territory_name": t_name,
            "current_owner": owner_name,
            "challenger_id": challenger_id,
            "status": "challenge_active",
            "instructions": instructions,
        }
    finally:
        await conn.close()
