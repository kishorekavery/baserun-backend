from fastapi import APIRouter, Depends, HTTPException, Query
from db.db_conn import sqlite_connection, fetch_record, execute_query, execute_query_one
from models.datamodel import (
    BaseListResponse,
    ClubItem,
    ClubLeaderboardMember,
    ClubLeaderboardResponse,
    ClubListResponse,
)
from routers.core import get_current_user

router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])


@router.get("", response_model=ClubListResponse)
async def list_clubs(
    user_id: str | None = Query(None, description="Check if user is a member"),
):
    """
    Screen 11: Clubs Screen - 'Discover' and 'My Club' tabs.
    Lists clubs like Chennai Runners, Anna Nagar Runners, Marina Striders.
    """
    conn = await sqlite_connection()
    try:
        rows = await execute_query(conn, "SELECT * FROM clubs ORDER BY member_count DESC")
        clubs = []
        user_club_id = None
        if user_id:
            member_row = await execute_query_one(conn, "SELECT club_id FROM club_members WHERE user_id = ?", (user_id,))
            if member_row:
                user_club_id = member_row["club_id"]

        for r in rows:
            clubs.append(ClubItem(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                avatar_url=r["avatar_url"],
                color=r["color"],
                member_count=r["member_count"],
                territory_count=r["territory_count"],
                total_km=round(r["total_km"], 1),
                is_joined=(r["id"] == user_club_id) if user_club_id else False,
            ))
        return {"total": len(clubs), "clubs": clubs}
    finally:
        await conn.close()


@router.get("/{club_id}", response_model=ClubItem)
async def get_club_details(club_id: int):
    """
    Screen 11: Get single club card details.
    """
    conn = await sqlite_connection()
    try:
        r = await fetch_record(conn, "clubs", "id", (club_id,))
        if not r:
            raise HTTPException(status_code=404, detail="Club not found")
        return ClubItem(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            avatar_url=r["avatar_url"],
            color=r["color"],
            member_count=r["member_count"],
            territory_count=r["territory_count"],
            total_km=round(r["total_km"], 1),
            is_joined=True,
        )
    finally:
        await conn.close()


@router.post("/{club_id}/join")
async def join_club(
    club_id: int,
    user_id: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 11: 'Join' club button.
    """
    actual_user_id = str(current_user["id"])
    conn = await sqlite_connection()
    try:
        club = await fetch_record(conn, "clubs", "id", (club_id,))
        if not club:
            raise HTTPException(status_code=404, detail="Club not found")

        # Upsert club membership
        await conn.execute("""
            INSERT INTO club_members (club_id, user_id, role)
            VALUES (?, ?, 'member')
            ON CONFLICT(club_id, user_id) DO NOTHING
        """, (club_id, actual_user_id))

        await conn.execute("UPDATE users SET club_id = ? WHERE id = ?", (club_id, int(actual_user_id) if actual_user_id.isdigit() else 1))
        await conn.execute("UPDATE clubs SET member_count = member_count + 1 WHERE id = ?", (club_id,))
        await conn.commit()


        return {"status": "success", "message": f"Successfully joined {club['name']}!"}
    finally:
        await conn.close()


@router.get("/{club_id}/leaderboard", response_model=ClubLeaderboardResponse)
async def get_club_leaderboard(
    club_id: int,
    current_user_id: str = Query("1"),
):
    """
    Screen 11: Club Leaderboard (Top contributors table)
    Ranks members by territories claimed and total KM:
    #1 runvicky (8 Territories | 120.4 KM)
    #2 priya_r (6 Territories | 98.7 KM)
    #3 adventurer99 (5 Territories | 76.2 KM)
    #4 karthik (4 Territories | 62.1 KM)
    #5 meena.run (4 Territories | 58.3 KM)
    """
    conn = await sqlite_connection()
    try:
        club = await fetch_record(conn, "clubs", "id", (club_id,))
        if not club:
            raise HTTPException(status_code=404, detail="Club not found")

        member_rows = await execute_query(
            conn,
            """
            SELECT 
                u.id as user_id,
                u.username,
                u.avatar_url,
                cm.territories_contributed,
                cm.km_contributed,
                (SELECT count(*) FROM claimed_bases cb WHERE cb.user_id = u.id AND cb.status = 'active') as active_territories,
                (SELECT COALESCE(SUM(r.distance_km), 0.0) FROM runs r WHERE r.user_id = u.id) as active_km
            FROM club_members cm
            JOIN users u ON cm.user_id = u.id
            WHERE cm.club_id = ?
            ORDER BY (cm.territories_contributed + (SELECT count(*) FROM claimed_bases cb WHERE cb.user_id = u.id AND cb.status = 'active')) DESC,
                     (cm.km_contributed + (SELECT COALESCE(SUM(r.distance_km), 0.0) FROM runs r WHERE r.user_id = u.id)) DESC
            LIMIT 5
            """,
            (club_id,),
        )

        members = []
        for idx, r in enumerate(member_rows):
            tot_terrs = (r["territories_contributed"] or 0) + (r["active_territories"] or 0)
            tot_km = round((r["km_contributed"] or 0.0) + (r["active_km"] or 0.0), 1)
            is_curr = str(r["user_id"]) == str(current_user_id)
            members.append(ClubLeaderboardMember(
                rank=idx + 1,
                user_id=str(r["user_id"]),
                username=r["username"],
                avatar_url=r["avatar_url"] or "",
                territories=tot_terrs,
                total_km=tot_km,
                is_current_user=is_curr,
            ))

        total_members_row = await execute_query_one(
            conn, "SELECT count(*) as cnt FROM club_members WHERE club_id = ?", (club_id,)
        )
        total_members = total_members_row["cnt"] if total_members_row else club["member_count"]

        total_terrs_row = await execute_query_one(
            conn, "SELECT count(*) as cnt FROM claimed_bases WHERE club_id = ? AND status = 'active'", (club_id,)
        )
        total_terrs = total_terrs_row["cnt"] if total_terrs_row else club["territory_count"]

        return {
            "club_id": club["id"],
            "club_name": club["name"],
            "total_members": total_members,
            "total_territories": total_terrs,
            "leaderboard": members,
        }
    finally:
        await conn.close()


@router.get("/{club_id}/territories", response_model=BaseListResponse)
async def get_club_territories(club_id: int):
    """
    Screen 11: Club territories tab.
    """
    conn = await sqlite_connection()
    try:
        rows = await execute_query(
            conn,
            "SELECT * FROM claimed_bases WHERE club_id = ? AND status = 'active' ORDER BY id DESC",
            (club_id,),
        )
        return {
            "total": len(rows),
            "bases": [dict(r) for r in rows],
        }
    finally:
        await conn.close()
