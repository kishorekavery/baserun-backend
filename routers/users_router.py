from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from db.database import connection, transaction
from models.datamodel import (
    AchievementsResponse,
    BaseListResponse,
    FullUserProfileResponse,
    IdentityResponse,
    RunHistoryResponse,
    UpdateIdentityRequest,
)
from routers.core import USER_PROFILE_SQL, get_current_user, get_current_user_optional, verify_ownership
from services.club_service import set_user_club
from services.territory_service import TERRITORY_SELECT, base_item
from utils.helper import format_duration, format_pace, iso, pace_seconds_per_km

router = APIRouter(prefix="/users", tags=["users"])

DEFAULT_AVATAR = "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=250&q=80"


async def _build_full_profile(user_id: int, include_email: bool) -> dict:
    async with connection() as conn:
        user = await conn.fetchrow(USER_PROFILE_SQL + " WHERE u.id = $1 AND u.is_active", user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        runs = await conn.fetchrow(
            """
            SELECT count(*) AS total_runs,
                   COALESCE(SUM(distance_km), 0) AS total_km,
                   COALESCE(SUM(duration_seconds), 0) AS total_seconds,
                   count(*) FILTER (WHERE claim_outcome IN ('success', 'takeover')) AS bases_claimed
            FROM runs WHERE user_id = $1 AND status = 'completed'
            """,
            user_id,
        )
        bases_held = await conn.fetchval(
            "SELECT count(*) FROM territories WHERE owner_id = $1 AND status = 'active'", user_id
        )
        favorite = await conn.fetchval(
            """
            SELECT name FROM territories WHERE owner_id = $1 AND status = 'active'
            ORDER BY total_runs DESC, area_km2 DESC LIMIT 1
            """,
            user_id,
        )

    total_km = round(runs["total_km"], 1)
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"] if include_email else None,
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "avatar_url": user["avatar_url"] or DEFAULT_AVATAR,
        "bio": user["bio"],
        "city": user["city"] or "",
        "territory_color": user["territory_color"],
        "total_territories": bases_held,
        "total_km": total_km,
        "total_runs": runs["total_runs"],
        "club_id": user["club_id"],
        "club_name": user["club_name"],
        "favorite_base": favorite,
        "stats": {
            "total_runs": runs["total_runs"],
            "total_km": total_km,
            "total_duration_hours": round(runs["total_seconds"] / 3600, 1),
            "bases_claimed": runs["bases_claimed"],
            "bases_held": bases_held,
            "avg_pace": format_pace(pace_seconds_per_km(runs["total_seconds"], runs["total_km"])),
        },
    }


async def _update_identity(user: dict, request: UpdateIdentityRequest) -> dict:
    fields = request.model_fields_set
    updates = {}
    if request.username is not None and request.username != user["username"]:
        updates["username"] = request.username
    for column in ("territory_color", "avatar_url", "bio", "first_name", "last_name", "city"):
        value = getattr(request, column)
        if value is not None:
            updates[column] = value.strip() if isinstance(value, str) else value

    async with transaction() as conn:
        if updates:
            assignments = ", ".join(f"{column} = ${i}" for i, column in enumerate(updates, start=2))
            try:
                await conn.execute(f"UPDATE users SET {assignments} WHERE id = $1", user["id"], *updates.values())
            except asyncpg.UniqueViolationError:
                raise HTTPException(status_code=400, detail="Username is already taken")
        # club_id: a value joins that club, an explicit null leaves the current club
        if "club_id" in fields and request.club_id != user["club_id"]:
            await set_user_club(conn, user["id"], request.club_id)
        updated = await conn.fetchrow(USER_PROFILE_SQL + " WHERE u.id = $1", user["id"])

    return {
        "user_id": str(updated["id"]),
        "username": updated["username"],
        "territory_color": updated["territory_color"],
        "avatar_url": updated["avatar_url"] or "",
        "bio": updated["bio"],
        "club_id": updated["club_id"],
        "club_name": updated["club_name"],
        "message": "Identity updated successfully",
    }


@router.get("/me", response_model=FullUserProfileResponse)
async def get_my_profile_me(current_user: dict = Depends(get_current_user)):
    """
    Screen 12: the authenticated runner's profile, lifetime stats and club.
    """
    return await _build_full_profile(current_user["id"], include_email=True)


@router.get("/profile", response_model=FullUserProfileResponse)
async def get_my_full_profile(current_user: dict = Depends(get_current_user)):
    """
    Screen 12: alias of /users/me.
    """
    return await _build_full_profile(current_user["id"], include_email=True)


@router.put("/identity", response_model=IdentityResponse)
async def update_identity(
    request: UpdateIdentityRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 4: choose username, territory color (#RRGGBB), avatar, bio and club.
    Territory colors are joined from the owner at read time, so a color
    change repaints all of the runner's bases immediately.
    """
    return await _update_identity(current_user, request)


@router.put("/{user_id}", response_model=IdentityResponse)
async def update_user(
    user_id: int,
    request: UpdateIdentityRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 4: same as PUT /users/identity, addressed by id (own account only).
    """
    verify_ownership(user_id, current_user["id"])
    return await _update_identity(current_user, request)


@router.get("/{user_id}/profile", response_model=FullUserProfileResponse)
async def get_user_profile_by_id(
    user_id: int,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Screen 12: public runner profile (email only shown to its owner).
    """
    return await _build_full_profile(user_id, include_email=bool(current_user and current_user["id"] == user_id))


@router.get("/{user_id}/runs", response_model=RunHistoryResponse)
async def get_user_runs(
    user_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 12: paginated run history (own runs only).
    """
    if current_user["id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to access these runs")

    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, activity_id, title, start_time, status, distance_km, duration_seconds,
                   calories, is_claimed, claim_outcome
            FROM runs WHERE user_id = $1
            ORDER BY start_time DESC, id DESC
            LIMIT $2 OFFSET $3
            """,
            user_id, limit, offset,
        )
        total = await conn.fetchval("SELECT count(*) FROM runs WHERE user_id = $1", user_id)

    runs = []
    for r in rows:
        claimed = r["claim_outcome"] in ("success", "takeover", "fortified")
        runs.append({
            "id": r["id"],
            "activity_id": r["activity_id"],
            "title": r["title"],
            "date": iso(r["start_time"]),
            "distance_km": round(r["distance_km"], 2),
            "duration": format_duration(r["duration_seconds"]),
            "duration_seconds": r["duration_seconds"],
            "pace": format_pace(pace_seconds_per_km(r["duration_seconds"], r["distance_km"])),
            "calories": r["calories"],
            "status": r["status"],
            "is_claimed": r["is_claimed"],
            "badge": "Claimed Base" if claimed else "Open Run",
        })
    return {"user_id": str(user_id), "total_runs": total, "limit": limit, "offset": offset, "runs": runs}


@router.get("/{user_id}/territories", response_model=BaseListResponse)
async def get_user_territories(user_id: int):
    """
    Screen 12: territories currently held by a runner.
    """
    async with connection() as conn:
        rows = await conn.fetch(
            TERRITORY_SELECT + " WHERE t.owner_id = $1 AND t.status = 'active' ORDER BY t.id DESC", user_id
        )
    return {"total": len(rows), "bases": [base_item(r) for r in rows]}


@router.get("/{user_id}/achievements", response_model=AchievementsResponse)
async def get_user_achievements(user_id: int, current_user: dict = Depends(get_current_user)):
    """
    Screen 12: unlocked badges of a runner.
    """
    async with connection() as conn:
        exists = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users WHERE id = $1)", user_id)
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")
        rows = await conn.fetch(
            """
            SELECT a.id, a.key, a.title, a.description, a.badge_icon, ua.unlocked_at
            FROM user_achievements ua JOIN achievements a ON a.id = ua.achievement_id
            WHERE ua.user_id = $1
            ORDER BY ua.unlocked_at, a.id
            """,
            user_id,
        )
    return {
        "user_id": str(user_id),
        "total_badges": len(rows),
        "achievements": [{**dict(r), "unlocked_at": iso(r["unlocked_at"])} for r in rows],
    }
