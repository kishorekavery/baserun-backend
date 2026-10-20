from fastapi import APIRouter, Depends, HTTPException
from db.db_conn import sqlite_connection, fetch_record, execute_query, execute_query_one
from models.datamodel import (
    AchievementsResponse,
    AchievementItem,
    FullUserProfileResponse,
    IdentityResponse,
    RunHistoryResponse,
    RunHistoryItem,
    BaseListResponse,
    UpdateIdentityRequest,
)
from routers.core import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=FullUserProfileResponse)
async def get_my_profile_me(current_user: dict = Depends(get_current_user)):
    """
    Flutter Profile Endpoint: GET /users/me
    Returns the authenticated runner's full profile, territory stats, and club info.
    """
    return await _build_full_profile(str(current_user["id"]))


@router.put("/identity", response_model=IdentityResponse)
async def update_identity(

    request: UpdateIdentityRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 4: Choose Your Identity
    Pick username and unique territory color swatch (e.g. #00E5FF) and avatar.
    """
    user_id = str(current_user["id"])
    conn = await sqlite_connection()
    try:
        # Check if new username is already taken by someone else
        if request.username and request.username != current_user["username"]:
            existing = await fetch_record(conn, "users", "username", (request.username,))
            if existing:
                raise HTTPException(status_code=400, detail="Username is already taken")
            await conn.execute("UPDATE users SET username = ? WHERE id = ?", (request.username, int(user_id)))

        if request.territory_color:
            await conn.execute("UPDATE users SET territory_color = ? WHERE id = ?", (request.territory_color, int(user_id)))

        if request.avatar_url:
            await conn.execute("UPDATE users SET avatar_url = ? WHERE id = ?", (request.avatar_url, int(user_id)))

        await conn.commit()

        updated = await fetch_record(conn, "users", "id", (int(user_id),))
        return {
            "user_id": user_id,
            "username": updated["username"],
            "territory_color": updated["territory_color"] or "#00E5FF",
            "avatar_url": updated["avatar_url"] or "",
            "message": "Identity updated successfully",
        }
    finally:
        await conn.close()


@router.get("/profile", response_model=FullUserProfileResponse)
async def get_my_full_profile(current_user: dict = Depends(get_current_user)):
    """
    Screen 12: Profile - Current logged-in runner profile with territory & running stats.
    """
    return await _build_full_profile(str(current_user["id"]))


@router.get("/{user_id}/profile", response_model=FullUserProfileResponse)
async def get_user_profile_by_id(user_id: str):
    """
    Screen 12: Profile - View runner profile by User ID.
    """
    return await _build_full_profile(user_id)


async def _build_full_profile(user_id: str) -> dict:
    conn = await sqlite_connection()
    try:
        user = await fetch_record(conn, "users", "id", (int(user_id),))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Aggregate territory count
        base_stats = await execute_query_one(
            conn,
            "SELECT count(*) as total_territories FROM claimed_bases WHERE user_id = ? AND status = 'active'",
            (user_id,),
        )
        total_territories = base_stats["total_territories"] if base_stats else 0

        # Aggregate run stats
        run_stats = await execute_query_one(
            conn,
            "SELECT count(*) as total_runs, COALESCE(SUM(distance_km), 0.0) as total_km FROM runs WHERE user_id = ?",
            (user_id,),
        )
        total_runs = run_stats["total_runs"] if run_stats else 0
        total_km = round(run_stats["total_km"], 1) if run_stats else 0.0

        # Club info
        club_name = None
        if user["club_id"]:
            club = await fetch_record(conn, "clubs", "id", (user["club_id"],))
            if club:
                club_name = club["name"]

        return {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "avatar_url": user["avatar_url"] or "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=250&q=80",
            "city": user["city"] or "Chennai, India",
            "territory_color": user["territory_color"] or "#00E5FF",
            "total_territories": total_territories,
            "total_km": total_km,
            "total_runs": total_runs,
            "club_id": user["club_id"],
            "club_name": club_name or "Chennai Runners",
        }
    finally:
        await conn.close()


@router.get("/{user_id}/runs", response_model=RunHistoryResponse)
async def get_user_runs(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 12: My Runs History
    """
    if str(current_user["id"]) != str(user_id):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: You do not have permission to access these runs",
        )

    conn = await sqlite_connection()
    try:
        rows = await execute_query(
            conn,
            "SELECT id, activity_id, title, start_time, distance_km, duration_seconds, pace_min_per_km, calories, status FROM runs WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        )
        runs = []
        for r in rows:
            minutes = r["duration_seconds"] // 60
            seconds = r["duration_seconds"] % 60
            runs.append(RunHistoryItem(
                id=r["id"],
                activity_id=r["activity_id"],
                title=r["title"] or "City Run",
                date=r["start_time"],
                distance_km=round(r["distance_km"], 2),
                duration=f"{minutes}:{seconds:02d}",
                pace=r["pace_min_per_km"] or "6'11\"",
                calories=r["calories"] or 0,
                status=r["status"],
            ))
        return {
            "user_id": user_id,
            "total_runs": len(runs),
            "runs": runs,
        }
    finally:
        await conn.close()


@router.get("/{user_id}/territories", response_model=BaseListResponse)
async def get_user_territories(user_id: str):
    """
    Screen 12: My Territories list
    """
    conn = await sqlite_connection()
    try:
        rows = await execute_query(
            conn,
            "SELECT * FROM claimed_bases WHERE user_id = ? AND status = 'active' ORDER BY id DESC",
            (user_id,),
        )
        return {
            "total": len(rows),
            "bases": [dict(r) for r in rows],
        }
    finally:
        await conn.close()


@router.get("/{user_id}/achievements", response_model=AchievementsResponse)
async def get_user_achievements(user_id: str):
    """
    Screen 12: Achievements (12 Badges)
    """
    conn = await sqlite_connection()
    try:
        rows = await execute_query(
            conn,
            "SELECT id, title, description, badge_icon, unlocked_at FROM achievements WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        )
        # If user has no unlocked badges yet, seed real starter achievements in DB
        if not rows:
            await conn.executemany("""
                INSERT INTO achievements (user_id, title, description, badge_icon)
                VALUES (?, ?, ?, ?)
            """, [
                (user_id, "First Step", "Started your running journey with Base Run", "footprints"),
                (user_id, "Cadence Initiate", "Recorded your first GPS telemetry", "activity"),
            ])
            await conn.commit()
            rows = await execute_query(
                conn,
                "SELECT id, title, description, badge_icon, unlocked_at FROM achievements WHERE user_id = ? ORDER BY id ASC",
                (user_id,),
            )

        return {
            "user_id": user_id,
            "total_badges": len(rows),
            "achievements": [dict(r) for r in rows],
        }
    finally:
        await conn.close()
