import asyncpg
from fastapi import HTTPException

FORBIDDEN_DETAIL = "Forbidden: You do not have permission to modify this resource"


def verify_ownership(owner_id: object, current_user_id: object) -> None:
    """
    Compares a resource owner to the caller and raises 403 on mismatch.
    """
    if owner_id is None or str(owner_id) != str(current_user_id):
        raise HTTPException(status_code=403, detail=FORBIDDEN_DETAIL)


async def get_activity_with_ownership(
    conn: asyncpg.Connection, activity_id: str, current_user_id: object, *, for_update: bool = False
) -> dict:
    """
    Loads a run by activity_id and verifies the caller owns it (404 / 403).
    """
    query = "SELECT * FROM runs WHERE activity_id = $1" + (" FOR UPDATE" if for_update else "")
    run = await conn.fetchrow(query, activity_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run activity not found")
    verify_ownership(run["user_id"], current_user_id)
    return dict(run)
