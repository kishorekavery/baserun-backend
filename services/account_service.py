"""
Account deletion. This is a real delete, not a soft flag: the runner's GPS
history is personal data. The foreign keys cascade to sessions, runs, telemetry,
friendships, achievements and the profile photo.
"""
from db.database import transaction
from services.territory_service import DEFAULT_NAME_SUFFIX, UNCLAIMED_NAME


async def delete_account(user_id: int) -> None:
    async with transaction() as conn:
        username = await conn.fetchval("SELECT username FROM users WHERE id = $1 FOR UPDATE", user_id)
        # Bases outlive their owner as open ground anyone can win with a matching
        # loop. The old record goes too: nobody should have to beat a deleted runner.
        await conn.execute(
            """
            UPDATE territories
            SET status = 'unclaimed', owner_id = NULL, club_id = NULL, record_pace_seconds = NULL,
                defense_level = 1,
                name = CASE WHEN name = $2 THEN $3 ELSE name END
            WHERE owner_id = $1
            """,
            user_id, f"{username}{DEFAULT_NAME_SUFFIX}", UNCLAIMED_NAME,
        )
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)
