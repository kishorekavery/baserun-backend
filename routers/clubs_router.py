from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from db.database import connection, transaction
from models.datamodel import (
    BaseListResponse,
    ClubItem,
    ClubLeaderboardResponse,
    ClubListResponse,
    ClubMembershipResponse,
)
from routers.core import get_current_user, get_current_user_optional
from services import privacy
from services.club_service import CLUB_STATS_SELECT, club_item, set_user_club
from services.territory_service import TERRITORY_SELECT, base_item
from utils.helper import format_pace, pace_seconds_per_km

router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])


async def _viewer_club_id(conn, current_user: Optional[dict], user_id: Optional[int]) -> Optional[int]:
    if current_user:
        return current_user["club_id"]
    # The legacy parameter is unverified: it must not reveal a hidden runner's club
    if user_id is not None and await privacy.is_visible(conn, user_id, None):
        return await conn.fetchval("SELECT club_id FROM users WHERE id = $1", user_id)
    return None


@router.get("", response_model=ClubListResponse)
async def list_clubs(
    user_id: Optional[int] = Query(None, description="Legacy: viewer id when no Bearer token is sent"),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Screen 11: clubs ranked by territories held, with live member/territory tallies.
    """
    async with connection() as conn:
        viewer_club_id = await _viewer_club_id(conn, current_user, user_id)
        rows = await conn.fetch(CLUB_STATS_SELECT + " ORDER BY territory_count DESC, member_count DESC, c.id")
    clubs = [club_item(r, viewer_club_id) for r in rows]
    return {"total": len(clubs), "clubs": clubs}


@router.get("/{club_id}", response_model=ClubItem)
async def get_club_details(
    club_id: int,
    user_id: Optional[int] = Query(None, description="Legacy: viewer id when no Bearer token is sent"),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Screen 11: single club card.
    """
    async with connection() as conn:
        viewer_club_id = await _viewer_club_id(conn, current_user, user_id)
        row = await conn.fetchrow(CLUB_STATS_SELECT + " WHERE c.id = $1", club_id)
    if not row:
        raise HTTPException(status_code=404, detail="Club not found")
    return club_item(row, viewer_club_id)


@router.post("/{club_id}/join", response_model=ClubMembershipResponse)
async def join_club(club_id: int, current_user: dict = Depends(get_current_user)):
    """
    Screen 11: join or switch club. Your active territories move with you.
    """
    async with transaction() as conn:
        club_name = await conn.fetchval("SELECT name FROM clubs WHERE id = $1", club_id)
        if club_name is None:
            raise HTTPException(status_code=404, detail="Club not found")
        transferred = await set_user_club(conn, current_user["id"], club_id)
    return {
        "status": "success",
        "message": f"Successfully joined {club_name}!",
        "club_id": club_id,
        "club_name": club_name,
        "territories_transferred": transferred,
    }


@router.post("/{club_id}/leave", response_model=ClubMembershipResponse)
async def leave_club(club_id: int, current_user: dict = Depends(get_current_user)):
    """
    Screen 11: leave your club and run as a "Solo Rogue".
    """
    if current_user["club_id"] != club_id:
        raise HTTPException(status_code=400, detail="You are not a member of this club")
    async with transaction() as conn:
        transferred = await set_user_club(conn, current_user["id"], None)
    return {
        "status": "success",
        "message": "You left the club",
        "club_id": None,
        "club_name": None,
        "territories_transferred": transferred,
    }


@router.get("/{club_id}/leaderboard", response_model=ClubLeaderboardResponse)
async def get_club_leaderboard(
    club_id: int,
    limit: int = Query(50, ge=1, le=200),
    current_user_id: Optional[int] = Query(None, description="Legacy: viewer id when no Bearer token is sent"),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Screen 11: club members ranked by bases held, then kilometres run.
    """
    viewer_id = current_user["id"] if current_user else current_user_id
    token_viewer_id = current_user["id"] if current_user else None
    async with connection() as conn:
        club = await conn.fetchrow(CLUB_STATS_SELECT + " WHERE c.id = $1", club_id)
        if not club:
            raise HTTPException(status_code=404, detail="Club not found")
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, u.avatar_url, u.avatar_thumb_url, u.privacy_level,
                   (SELECT count(*) FROM territories t
                     WHERE t.owner_id = u.id AND t.club_id = $1 AND t.status = 'active') AS territories_held,
                   COALESCE(rs.total_km, 0) AS total_km,
                   COALESCE(rs.total_seconds, 0) AS total_seconds
            FROM users u
            LEFT JOIN (
                SELECT user_id, SUM(distance_km) AS total_km, SUM(duration_seconds) AS total_seconds
                FROM runs WHERE status = 'completed' GROUP BY user_id
            ) rs ON rs.user_id = u.id
            WHERE u.club_id = $1 AND u.is_active
            ORDER BY territories_held DESC, total_km DESC, u.id
            LIMIT $2
            """,
            club_id, limit,
        )
        friends = await privacy.friends_among(
            conn, token_viewer_id, [r["id"] for r in rows if r["privacy_level"] != privacy.PUBLIC]
        )

    leaderboard = []
    for rank, r in enumerate(rows, start=1):
        # Hidden runners keep their rank and totals, which then belong to nobody in particular
        visible = privacy.can_view(r["privacy_level"], r["id"], token_viewer_id, r["id"] in friends)
        avatar = (r["avatar_url"] or "") if visible else ""
        name = r["username"] if visible else privacy.PRIVATE_RUNNER
        leaderboard.append({
            "rank": rank,
            "user_id": str(r["id"]) if visible else "",
            "username": name,
            "runner_name": name,
            "avatar_url": avatar,
            "avatar_thumb_url": (r["avatar_thumb_url"] or "") if visible else "",
            "runner_avatar": avatar,
            "territories": r["territories_held"],
            "territories_held": r["territories_held"],
            "total_km": round(r["total_km"], 1),
            "avg_pace": format_pace(pace_seconds_per_km(r["total_seconds"], r["total_km"])),
            "avg_pace_seconds": pace_seconds_per_km(r["total_seconds"], r["total_km"]),
            "is_current_user": visible and r["id"] == viewer_id,
        })
    return {
        "club_id": club["id"],
        "club_name": club["name"],
        "total_members": club["member_count"],
        "total_territories": club["territory_count"],
        "leaderboard": leaderboard,
    }


@router.get("/{club_id}/territories", response_model=BaseListResponse)
async def get_club_territories(club_id: int, current_user: Optional[dict] = Depends(get_current_user_optional)):
    """
    Screen 11: active territories held by a club.
    """
    async with connection() as conn:
        rows = await conn.fetch(
            TERRITORY_SELECT + " WHERE t.club_id = $1 AND t.status = 'active' ORDER BY t.id DESC", club_id
        )
        rows = await privacy.mask_territories(conn, rows, current_user["id"] if current_user else None)
    return {"total": len(rows), "bases": [base_item(r) for r in rows]}
