from fastapi import APIRouter, HTTPException, Query
from db.db_conn import sqlite_connection, fetch_record, execute_query, execute_query_one
from models.datamodel import (
    BaseListResponse,
    ClubItem,
    ClubLeaderboardMember,
    ClubLeaderboardResponse,
    ClubListResponse,
)

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
async def join_club(club_id: int, user_id: str = Query("1")):
    """
    Screen 11: 'Join' club button.
    """
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
        """, (club_id, user_id))

        await conn.execute("UPDATE users SET club_id = ? WHERE id = ?", (club_id, int(user_id) if user_id.isdigit() else 1))
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

        # Mock rich top 5 contributors from design
        members = [
            ClubLeaderboardMember(rank=1, user_id="101", username="runvicky", avatar_url="https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=150&q=80", territories=8, total_km=120.4, is_current_user=False),
            ClubLeaderboardMember(rank=2, user_id="102", username="priya_r", avatar_url="https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=150&q=80", territories=6, total_km=98.7, is_current_user=False),
            ClubLeaderboardMember(rank=3, user_id=str(current_user_id), username="adventurer99", avatar_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=150&q=80", territories=5, total_km=76.2, is_current_user=True),
            ClubLeaderboardMember(rank=4, user_id="103", username="karthik", avatar_url="https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=150&q=80", territories=4, total_km=62.1, is_current_user=False),
            ClubLeaderboardMember(rank=5, user_id="104", username="meena.run", avatar_url="https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=150&q=80", territories=4, total_km=58.3, is_current_user=False),
        ]

        return {
            "club_id": club["id"],
            "club_name": club["name"],
            "total_members": club["member_count"],
            "total_territories": club["territory_count"],
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
