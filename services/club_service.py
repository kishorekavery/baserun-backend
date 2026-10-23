from typing import Optional

import asyncpg
from fastapi import HTTPException

CLUB_STATS_SELECT = """
    SELECT c.*,
           (SELECT count(*) FROM users u WHERE u.club_id = c.id AND u.is_active) AS member_count,
           COALESCE(t.territory_count, 0) AS territory_count,
           COALESCE(t.total_area_km2, 0) AS total_area_km2,
           (SELECT COALESCE(SUM(r.distance_km), 0)
              FROM runs r JOIN users u ON u.id = r.user_id
             WHERE u.club_id = c.id AND r.status = 'completed') AS total_km
    FROM clubs c
    LEFT JOIN (
        SELECT club_id, count(*) AS territory_count, SUM(area_km2) AS total_area_km2
        FROM territories WHERE status = 'active' GROUP BY club_id
    ) t ON t.club_id = c.id
"""


def club_item(row, viewer_club_id: Optional[int]) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "tagline": row["tagline"] or "",
        "description": row["description"] or row["tagline"] or "",
        "avatar_url": row["avatar_url"] or "",
        "color": row["color"],
        "member_count": row["member_count"],
        "territory_count": row["territory_count"],
        "total_area_km2": round(row["total_area_km2"], 4),
        "total_km": round(row["total_km"], 1),
        "is_joined": viewer_club_id is not None and row["id"] == viewer_club_id,
    }


async def set_user_club(conn: asyncpg.Connection, user_id: int, club_id: Optional[int]) -> int:
    """
    Moves a user to `club_id` (None = leave) and re-assigns the user's active
    territories to the new club. Must run inside a transaction.
    Returns the number of territories transferred.
    """
    if club_id is not None:
        exists = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM clubs WHERE id = $1)", club_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Club not found")
    await conn.execute("UPDATE users SET club_id = $2 WHERE id = $1", user_id, club_id)
    result = await conn.execute(
        """
        UPDATE territories SET club_id = $2
        WHERE owner_id = $1 AND status = 'active' AND club_id IS DISTINCT FROM $2
        """,
        user_id, club_id,
    )
    return int(result.split()[-1])
