"""
Territory minting, overlap resolution (fortify / takeover) and serialisation.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Sequence

import asyncpg
from fastapi import HTTPException

from db.database import transaction
from models.datamodel import ClaimFromRunRequest, FinishRunRequest, LocationPoint
from services import achievements, run_service
from utils.geo import (
    MAX_TERRITORY_AREA_KM2,
    PolygonMetrics,
    load_polygon,
    overlap_ratio,
    polygon_from_points,
    polygon_metrics,
)
from utils.helper import format_pace, iso, utcnow

TAKEOVER_OVERLAP = 0.75
DEFENSE_DECAY_DAYS = 7
CLUB_HERO_BASES = 5
LEGACY_MIN_AREA_M2 = 100
LEGACY_MAX_AREA_M2 = 500_000
DEFAULT_COLOR = "#00E5FF"
DEFAULT_NAME_SUFFIX = "'s Base"
UNCLAIMED_NAME = "Unclaimed Base"

TERRITORY_SELECT = """
    SELECT t.*,
           u.username AS owner_username, u.avatar_url AS owner_avatar, u.territory_color AS owner_color,
           u.privacy_level AS owner_privacy,
           c.name AS club_name, c.color AS club_color
    FROM territories t
    LEFT JOIN users u ON u.id = t.owner_id
    LEFT JOIN clubs c ON c.id = t.club_id
"""


def effective_defense_level(row, now: Optional[datetime] = None) -> int:
    """Bases lose one defense level per week without a defense run (minimum 1)."""
    now = now or utcnow()
    idle_days = max((now - row["last_defended_at"]).days, 0)
    return max(1, (row["defense_level"] or 1) - idle_days // DEFENSE_DECAY_DAYS)


def base_item(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "user_id": str(row["owner_id"]) if row["owner_id"] is not None else "",
        "club_id": row["club_id"],
        "status": row["status"],
        "area": round(row["area_km2"] * 1_000_000, 2),
        "perimeter": round(row["perimeter_km"] * 1000, 2),
        "area_km2": row["area_km2"],
        "perimeter_km": row["perimeter_km"],
        "owner_color": row["owner_color"] or DEFAULT_COLOR,
        "polygon_wkt": row["polygon_wkt"],
        "created_at": iso(row["created_at"]),
    }


async def insert_territory(
    conn: asyncpg.Connection,
    *,
    name: str,
    owner_id: int,
    club_id: Optional[int],
    run_id: Optional[int],
    metrics: PolygonMetrics,
    record_pace_seconds: Optional[int],
    total_km: float,
) -> int:
    polygon = metrics.polygon
    min_lng, min_lat, max_lng, max_lat = polygon.bounds
    centroid = polygon.centroid
    return await conn.fetchval(
        """
        INSERT INTO territories (
            name, owner_id, club_id, origin_run_id, area_km2, perimeter_km, polygon_wkt,
            min_lat, min_lng, max_lat, max_lng, centroid_lat, centroid_lng,
            record_pace_seconds, total_km
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        RETURNING id
        """,
        name, owner_id, club_id, run_id, metrics.area_km2, metrics.perimeter_km, metrics.wkt,
        min_lat, min_lng, max_lat, max_lng, centroid.y, centroid.x,
        record_pace_seconds, total_km,
    )


async def _best_overlap(conn: asyncpg.Connection, metrics: PolygonMetrics):
    """Territory that is essentially the same loop (> 75% mutual overlap), row-locked."""
    min_lng, min_lat, max_lng, max_lat = metrics.polygon.bounds
    candidates = await conn.fetch(
        """
        SELECT * FROM territories
        WHERE status IN ('active', 'unclaimed')
          AND max_lat >= $1 AND min_lat <= $2 AND max_lng >= $3 AND min_lng <= $4
        ORDER BY id
        FOR UPDATE
        """,
        min_lat, max_lat, min_lng, max_lng,
    )
    best, best_ratio = None, TAKEOVER_OVERLAP
    for row in candidates:
        existing = load_polygon(row["polygon_wkt"])
        if existing is None:
            continue
        # Share of the larger polygon covered by the intersection
        larger = existing if existing.area >= metrics.polygon.area else metrics.polygon
        smaller = metrics.polygon if larger is existing else existing
        ratio = overlap_ratio(smaller, larger)
        if ratio > best_ratio:
            best, best_ratio = dict(row), ratio
    return best


async def _resolve_challenges(conn: asyncpg.Connection, territory_id: int, challenger_id: int, won: bool) -> None:
    await conn.execute(
        """
        UPDATE challenges SET status = $3, resolved_at = NOW()
        WHERE territory_id = $1 AND challenger_id = $2 AND status = 'active'
        """,
        territory_id, challenger_id, "won" if won else "lost",
    )


async def claim_from_run(user: dict, request: ClaimFromRunRequest) -> dict:
    now = utcnow()
    async with transaction() as conn:
        run, _ = await run_service.get_or_create_run(
            conn, user, request.activity_id, title=request.territory_name
        )
        if run["is_claimed"]:
            raise HTTPException(status_code=400, detail="This run has already been claimed as a territory")

        if run["status"] != run_service.COMPLETED:
            result = await run_service.finalize_run(conn, user, run, FinishRunRequest(points=request.points))
            run, badges = result.run, list(result.badges)
        else:
            badges = []
            inserted = await run_service.insert_points(conn, run, run_service.filter_points(request.points))
            if inserted:
                run = await run_service.recompute_geometry(conn, run)

        if not run["is_loop_closed"] or not run["loop_polygon_wkt"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Loop was not closed. A claim needs at least 300 m of running that ends within "
                    "50 m of the start and at least 3 GPS points enclosing an area."
                ),
            )
        polygon = load_polygon(run["loop_polygon_wkt"])
        if polygon is None:
            raise HTTPException(status_code=400, detail="Run path does not enclose a valid area")
        metrics = polygon_metrics(polygon)
        if metrics.area_km2 > MAX_TERRITORY_AREA_KM2:
            raise HTTPException(status_code=400, detail=f"Territory is too large (max {MAX_TERRITORY_AREA_KM2} km²)")

        pace = run["avg_pace_seconds"] or None
        distance_km = run["distance_km"] or 0.0
        name = (request.territory_name or "").strip() or f"{user['username']}{DEFAULT_NAME_SUFFIX}"
        existing = await _best_overlap(conn, metrics)
        keys: List[str] = []

        if existing is None:
            outcome = "success"
            territory_id = await insert_territory(
                conn, name=name, owner_id=user["id"], club_id=user["club_id"], run_id=run["id"],
                metrics=metrics, record_pace_seconds=pace, total_km=distance_km,
            )
            message = f"You now hold {name}!"
        elif existing["owner_id"] == user["id"]:
            outcome = "fortified"
            territory_id = existing["id"]
            await conn.execute(
                """
                UPDATE territories
                SET defense_level = $2, times_defended = times_defended + 1, last_defended_at = $3,
                    record_pace_seconds = CASE WHEN $4::int IS NULL THEN record_pace_seconds
                                               ELSE LEAST(COALESCE(record_pace_seconds, $4::int), $4::int) END,
                    total_runs = total_runs + 1, total_km = total_km + $5
                WHERE id = $1
                """,
                territory_id, effective_defense_level(existing, now) + 1, now, pace, distance_km,
            )
            message = f"You fortified {existing['name']}!"
        else:
            territory_id = existing["id"]
            incumbent = existing["record_pace_seconds"]
            ownerless = existing["owner_id"] is None
            faster = ownerless or (pace is not None and (incumbent is None or pace < incumbent))
            # Default names carry the holder's username, which may be private
            default_name = existing["name"].endswith(DEFAULT_NAME_SUFFIX) or existing["name"] == UNCLAIMED_NAME
            label = "this base" if default_name else existing["name"]
            if faster:
                outcome = "takeover"
                await conn.execute(
                    """
                    UPDATE territories
                    SET owner_id = $2, club_id = $3, origin_run_id = $4, record_pace_seconds = $5,
                        defense_level = 1, conquered_at = $6, last_defended_at = $6, status = 'active',
                        times_challenged = times_challenged + 1, total_runs = total_runs + 1,
                        total_km = total_km + $7, name = $8
                    WHERE id = $1
                    """,
                    territory_id, user["id"], user["club_id"], run["id"], pace, now, distance_km,
                    (request.territory_name or "").strip() or (name if default_name else existing["name"]),
                )
                if not ownerless:
                    keys.append("hostile_takeover")
                message = f"You captured {label}!"
            else:
                outcome = "challenge_failed"
                await conn.execute(
                    """
                    UPDATE territories
                    SET times_challenged = times_challenged + 1, times_defended = times_defended + 1,
                        last_defended_at = $2
                    WHERE id = $1
                    """,
                    territory_id, now,
                )
                message = (
                    f"Too slow! {label} is defended by a {format_pace(incumbent)} /km record pace."
                )
            await _resolve_challenges(conn, territory_id, user["id"], won=faster)

        await conn.execute(
            "UPDATE runs SET is_claimed = TRUE, claim_outcome = $2 WHERE id = $1", run["id"], outcome
        )

        if outcome in ("success", "takeover"):
            keys.insert(0, "first_conquest")
            if distance_km >= 5:
                keys.append("5k_conqueror")
        if user["club_id"] is not None:
            held_for_club = await conn.fetchval(
                "SELECT count(*) FROM territories WHERE owner_id = $1 AND club_id = $2 AND status = 'active'",
                user["id"], user["club_id"],
            )
            if held_for_club >= CLUB_HERO_BASES:
                keys.append("club_hero")
        badges += await achievements.award(conn, user["id"], keys)

        territory = await conn.fetchrow(TERRITORY_SELECT + " WHERE t.id = $1", territory_id)

    share_text = (
        f"I just claimed {territory['name']} ({territory['area_km2']:.2f} km²) on BASER! "
        "Can you beat my pace? #RunToClaim"
    )
    return {
        "status": outcome,
        "message": message,
        "territory_id": territory["id"],
        "territory_name": territory["name"],
        "area_km2": territory["area_km2"],
        "perimeter_km": territory["perimeter_km"],
        "owner_username": territory["owner_username"] or "",
        "owner_avatar": territory["owner_avatar"] or "",
        "polygon_wkt": territory["polygon_wkt"],
        "defense_level": effective_defense_level(territory),
        "badge_earned": badges[0] if badges else None,
        "badges_earned": badges,
        "share_text": share_text,
    }


async def claim_raw_polygon(user: dict, points: Sequence[LocationPoint]) -> dict:
    """Legacy /api/v1/bases/claim: mint a territory straight from a point list."""
    if len(points) < 5:
        raise HTTPException(status_code=400, detail="not enough points to claim an area")
    metrics = polygon_from_points([(p.latitude, p.longitude) for p in points])
    if metrics is None:
        raise HTTPException(status_code=400, detail="Invalid path geometry")
    area_m2 = round(metrics.area_km2 * 1_000_000, 2)
    if area_m2 < LEGACY_MIN_AREA_M2:
        return {"status": "failed", "message": "Base is too small", "area": area_m2}
    if area_m2 > LEGACY_MAX_AREA_M2:
        return {"status": "failed", "message": "Base is too large", "area": area_m2}

    async with transaction() as conn:
        base_id = await insert_territory(
            conn, name=f"{user['username']}{DEFAULT_NAME_SUFFIX}", owner_id=user["id"], club_id=user["club_id"],
            run_id=None, metrics=metrics, record_pace_seconds=None, total_km=metrics.perimeter_km,
        )
    return {
        "status": "success",
        "message": "Area claimed successfully",
        "base_id": base_id,
        "area": area_m2,
        "perimeter": round(metrics.perimeter_km * 1000, 2),
        "polygon_wkt": metrics.wkt,
    }


def new_challenge_id() -> str:
    return f"chal_{uuid.uuid4().hex[:12]}"
