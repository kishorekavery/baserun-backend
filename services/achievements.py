from typing import List, Sequence

import asyncpg


async def award(conn: asyncpg.Connection, user_id: int, keys: Sequence[str]) -> List[str]:
    """
    Unlocks the given achievement keys for a user (no-op for ones already held).
    Returns the titles that were newly unlocked, in the order of `keys`.
    """
    if not keys:
        return []
    rows = await conn.fetch(
        """
        WITH unlocked AS (
            INSERT INTO user_achievements (user_id, achievement_id)
            SELECT $1, id FROM achievements WHERE key = ANY($2::text[])
            ON CONFLICT (user_id, achievement_id) DO NOTHING
            RETURNING achievement_id
        )
        SELECT a.key, a.title FROM unlocked JOIN achievements a ON a.id = unlocked.achievement_id
        """,
        user_id, list(keys),
    )
    titles = {row["key"]: row["title"] for row in rows}
    return [titles[key] for key in keys if key in titles]
