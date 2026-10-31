"""
Who may see a runner's details.

    public   everyone (the default)
    friends  the runner and their friends
    private  only the runner

Bases stay on the map at every level, because they are the game, but a hidden
owner's name, avatar and color are removed so a base can't be traced back to
them. Hidden runners keep a minimal public card (username, avatar, color).

The viewer must always come from the verified Bearer token, never from the
legacy `user_id` / `current_user_id` query parameters: those can be spoofed.
"""
from typing import Iterable, List, Optional, Set

import asyncpg
from fastapi import HTTPException

from services.territory_service import DEFAULT_NAME_SUFFIX

PUBLIC, FRIENDS, PRIVATE = "public", "friends", "private"
PRIVATE_RUNNER = "Private runner"
PRIVATE_BASE = "Private Base"


def can_view(level: Optional[str], owner_id: int, viewer_id: Optional[int], is_friend: bool) -> bool:
    if level in (None, PUBLIC) or owner_id == viewer_id:
        return True
    return level == FRIENDS and is_friend


async def friends_among(conn: asyncpg.Connection, viewer_id: Optional[int], user_ids: Iterable[int]) -> Set[int]:
    """The subset of user_ids who are friends with the viewer."""
    ids = list(set(user_ids))
    if viewer_id is None or not ids:
        return set()
    rows = await conn.fetch(
        "SELECT friend_id FROM friendships WHERE user_id = $1 AND friend_id = ANY($2::int[])", viewer_id, ids
    )
    return {r["friend_id"] for r in rows}


async def is_visible(conn: asyncpg.Connection, owner_id: int, viewer_id: Optional[int]) -> bool:
    level = await conn.fetchval("SELECT privacy_level FROM users WHERE id = $1", owner_id)
    if can_view(level, owner_id, viewer_id, False):
        return True
    return level == FRIENDS and owner_id in await friends_among(conn, viewer_id, [owner_id])


async def require_visible(conn: asyncpg.Connection, owner_id: int, viewer_id: Optional[int]) -> None:
    if not await is_visible(conn, owner_id, viewer_id):
        raise HTTPException(status_code=403, detail="This runner's profile is private")


def _hide_owner(row) -> dict:
    name = row["name"]
    if name == f"{row['owner_username']}{DEFAULT_NAME_SUFFIX}":
        name = PRIVATE_BASE
    return {
        **dict(row),
        "name": name,
        "owner_id": None,
        "owner_username": PRIVATE_RUNNER,
        "owner_avatar": None,
        "owner_color": None,
    }


async def mask_territories(conn: asyncpg.Connection, rows: Iterable, viewer_id: Optional[int]) -> List[dict]:
    """
    Removes the owner from territories whose owner the viewer may not see.
    Rows need `owner_privacy`. Costs a query only when a row's owner is non-public.
    """
    rows = list(rows)
    restricted = [r["owner_id"] for r in rows if r["owner_privacy"] not in (None, PUBLIC)]
    friends = await friends_among(conn, viewer_id, restricted)
    return [
        dict(r) if r["owner_id"] is None or can_view(r["owner_privacy"], r["owner_id"], viewer_id, r["owner_id"] in friends)
        else _hide_owner(r)
        for r in rows
    ]
