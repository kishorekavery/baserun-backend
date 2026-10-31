"""
Friend requests and friendships.

Friendships are stored symmetrically ((a, b) and (b, a)); a partial unique
index allows at most one pending request per pair in either direction.
"""
from typing import Dict, Iterable, List, Optional

import asyncpg
from fastapi import HTTPException

from db.database import connection, transaction
from models.datamodel import SendFriendRequest
from services import privacy
from utils.helper import iso

MAX_PENDING_OUTGOING = 50
MAX_SENDS_PER_HOUR = 30

NONE, FRIENDS, OUTGOING, INCOMING, SELF = "none", "friends", "outgoing_pending", "incoming_pending", "self"

# $1 = caller id. friendship_status / request_id for every row come from the
# two LEFT JOINs, so a page of runners needs a single query.
RUNNER_SUMMARY_SELECT = """
    SELECT u.id AS user_id, u.username, u.first_name, u.last_name, u.avatar_url, u.avatar_thumb_url, u.territory_color,
           u.privacy_level, c.name AS club_name,
           COALESCE((SELECT SUM(r.distance_km) FROM runs r
                      WHERE r.user_id = u.id AND r.status = 'completed'), 0) AS total_km,
           (SELECT count(*) FROM territories t WHERE t.owner_id = u.id AND t.status = 'active') AS bases_held,
           f.created_at AS friends_since,
           fr.id AS request_id, fr.from_user_id AS request_from
    FROM users u
    LEFT JOIN clubs c ON c.id = u.club_id
    LEFT JOIN friendships f ON f.user_id = $1 AND f.friend_id = u.id
    LEFT JOIN friend_requests fr ON fr.status = 'pending' AND (
        (fr.from_user_id = $1 AND fr.to_user_id = u.id) OR (fr.from_user_id = u.id AND fr.to_user_id = $1)
    )
"""


def _status(row, me: int) -> str:
    if row["user_id"] == me:
        return SELF
    if row["friends_since"] is not None:
        return FRIENDS
    if row["request_id"] is not None:
        return OUTGOING if row["request_from"] == me else INCOMING
    return NONE


def runner_summary(row, me: int, include_friends_since: bool = False) -> dict:
    status = _status(row, me)
    visible = privacy.can_view(row["privacy_level"], row["user_id"], me, status == FRIENDS)
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "first_name": row["first_name"] if visible else None,
        "last_name": row["last_name"] if visible else None,
        "avatar_url": row["avatar_url"],
        "avatar_thumb_url": row["avatar_thumb_url"],
        "territory_color": row["territory_color"],
        "club_name": row["club_name"] if visible else None,
        "total_km": round(row["total_km"], 1) if visible else None,
        "bases_held": row["bases_held"] if visible else None,
        "friendship_status": status,
        "request_id": row["request_id"] if status in (OUTGOING, INCOMING) else None,
        "friends_since": iso(row["friends_since"]) if include_friends_since else None,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_runners(me: int, query: str, limit: int) -> dict:
    q = query.strip()
    if len(q) < 2:
        raise HTTPException(status_code=422, detail="Search query must be at least 2 characters")
    escaped = _escape_like(q.lower())
    async with connection() as conn:
        rows = await conn.fetch(
            RUNNER_SUMMARY_SELECT + """
            WHERE u.is_active
              AND (u.privacy_level <> 'private' OR f.user_id IS NOT NULL OR u.id = $1)
              AND (
                LOWER(u.username) LIKE '%' || $2 || '%'
                -- Matching a hidden real name would confirm who is behind a username
                OR ((u.privacy_level = 'public' OR f.user_id IS NOT NULL OR u.id = $1) AND (
                    LOWER(u.first_name) LIKE '%' || $2 || '%'
                    OR LOWER(COALESCE(u.last_name, '')) LIKE '%' || $2 || '%'
                ))
              )
            ORDER BY (LOWER(u.username) = $3) DESC,
                     (f.user_id IS NOT NULL) DESC,
                     (LOWER(u.username) LIKE $2 || '%') DESC,
                     LOWER(u.username)
            LIMIT $4
            """,
            me, escaped, q.lower(), limit,
        )
    return {"query": q, "results": [runner_summary(r, me) for r in rows]}


async def list_friends(me: int) -> dict:
    async with connection() as conn:
        rows = await conn.fetch(
            RUNNER_SUMMARY_SELECT + " WHERE f.user_id IS NOT NULL ORDER BY LOWER(u.username)", me
        )
        incoming = await conn.fetchval(
            "SELECT count(*) FROM friend_requests WHERE to_user_id = $1 AND status = 'pending'", me
        )
    return {
        "total": len(rows),
        "incoming_pending_count": incoming,
        "friends": [runner_summary(r, me, include_friends_since=True) for r in rows],
    }


async def _summaries(conn: asyncpg.Connection, me: int, user_ids: Iterable[int]) -> Dict[int, dict]:
    rows = await conn.fetch(RUNNER_SUMMARY_SELECT + " WHERE u.id = ANY($2::int[])", me, list(set(user_ids)))
    return {r["user_id"]: runner_summary(r, me) for r in rows}


async def list_requests(me: int) -> dict:
    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, from_user_id, to_user_id, status, created_at FROM friend_requests
            WHERE status = 'pending' AND (from_user_id = $1 OR to_user_id = $1)
            ORDER BY created_at DESC, id DESC
            """,
            me,
        )
        others = [r["to_user_id"] if r["from_user_id"] == me else r["from_user_id"] for r in rows]
        users = await _summaries(conn, me, others)

    incoming: List[dict] = []
    outgoing: List[dict] = []
    for row, other in zip(rows, others):
        if other not in users:
            continue
        outgoing_request = row["from_user_id"] == me
        item = {
            "id": row["id"],
            "direction": "outgoing" if outgoing_request else "incoming",
            "status": row["status"],
            "user": users[other],
            "created_at": iso(row["created_at"]),
        }
        (outgoing if outgoing_request else incoming).append(item)
    return {"incoming": incoming, "outgoing": outgoing}


async def _accept(conn: asyncpg.Connection, request) -> None:
    await conn.execute(
        "UPDATE friend_requests SET status = 'accepted', responded_at = NOW() WHERE id = $1", request["id"]
    )
    await conn.execute(
        """
        INSERT INTO friendships (user_id, friend_id) VALUES ($1, $2), ($2, $1)
        ON CONFLICT DO NOTHING
        """,
        request["from_user_id"], request["to_user_id"],
    )


async def _pending_between(conn: asyncpg.Connection, a: int, b: int):
    return await conn.fetchrow(
        """
        SELECT * FROM friend_requests
        WHERE status = 'pending'
          AND ((from_user_id = $1 AND to_user_id = $2) OR (from_user_id = $2 AND to_user_id = $1))
        FOR UPDATE
        """,
        a, b,
    )


def _friends_now(username: str) -> dict:
    return {"message": f"You are now friends with @{username}", "friendship_status": FRIENDS, "request_id": None}


async def send_request(user: dict, request: SendFriendRequest) -> dict:
    me = user["id"]
    if request.to_user_id is not None and request.to_user_id == me:
        raise HTTPException(status_code=400, detail="You can't add yourself")

    async with transaction() as conn:
        if request.to_user_id is not None:
            target = await conn.fetchrow(
                "SELECT id, username FROM users WHERE id = $1 AND is_active", request.to_user_id
            )
        else:
            target = await conn.fetchrow(
                "SELECT id, username FROM users WHERE LOWER(username) = LOWER($1) AND is_active",
                request.username.strip(),
            )
        if target is not None and target["id"] == me:
            raise HTTPException(status_code=400, detail="You can't add yourself")
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        other, username = target["id"], target["username"]

        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM friendships WHERE user_id = $1 AND friend_id = $2)", me, other):
            raise HTTPException(status_code=409, detail="You are already friends")

        pending = await _pending_between(conn, me, other)
        if pending is not None:
            if pending["from_user_id"] == me:
                raise HTTPException(status_code=409, detail="Friend request already sent")
            await _accept(conn, pending)
            return _friends_now(username)

        limits = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
                   count(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') AS last_hour
            FROM friend_requests WHERE from_user_id = $1
            """,
            me,
        )
        if limits["pending"] >= MAX_PENDING_OUTGOING:
            raise HTTPException(status_code=429, detail=f"You have {MAX_PENDING_OUTGOING} pending requests; wait for some to be answered")
        if limits["last_hour"] >= MAX_SENDS_PER_HOUR:
            raise HTTPException(status_code=429, detail="Too many friend requests, please try again later")

        try:
            async with conn.transaction():  # savepoint: a unique violation must not abort the outer transaction
                request_id = await conn.fetchval(
                    "INSERT INTO friend_requests (from_user_id, to_user_id) VALUES ($1, $2) RETURNING id", me, other
                )
        except asyncpg.UniqueViolationError:
            # The other runner sent a request at the same moment
            pending = await _pending_between(conn, me, other)
            if pending is None or pending["from_user_id"] == me:
                raise HTTPException(status_code=409, detail="Friend request already sent")
            await _accept(conn, pending)
            return _friends_now(username)

    return {"message": f"Friend request sent to @{username}", "friendship_status": OUTGOING, "request_id": request_id}


async def _load_pending_for(conn: asyncpg.Connection, request_id: int, me: int, role: str):
    """Row-locks a request and checks the caller's role ('to' = recipient, 'from' = sender)."""
    row = await conn.fetchrow(
        """
        SELECT fr.*, u.username AS from_username, t.username AS to_username
        FROM friend_requests fr
        JOIN users u ON u.id = fr.from_user_id
        JOIN users t ON t.id = fr.to_user_id
        WHERE fr.id = $1
        FOR UPDATE OF fr
        """,
        request_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Friend request not found")
    if row[f"{role}_user_id"] != me:
        action = "respond to" if role == "to" else "withdraw"
        raise HTTPException(status_code=403, detail=f"Forbidden: You cannot {action} this friend request")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="Request is no longer pending")
    return row


async def accept_request(user: dict, request_id: int) -> dict:
    async with transaction() as conn:
        row = await _load_pending_for(conn, request_id, user["id"], "to")
        await _accept(conn, row)
    return {**_friends_now(row["from_username"]), "request_id": request_id}


async def decline_request(user: dict, request_id: int) -> dict:
    async with transaction() as conn:
        await _load_pending_for(conn, request_id, user["id"], "to")
        await conn.execute(
            "UPDATE friend_requests SET status = 'declined', responded_at = NOW() WHERE id = $1", request_id
        )
    return {"message": "Friend request declined", "friendship_status": NONE, "request_id": request_id}


async def withdraw_request(user: dict, request_id: int) -> dict:
    async with transaction() as conn:
        row = await _load_pending_for(conn, request_id, user["id"], "from")
        await conn.execute(
            "UPDATE friend_requests SET status = 'cancelled', responded_at = NOW() WHERE id = $1", request_id
        )
    return {
        "message": f"Friend request to @{row['to_username']} withdrawn",
        "friendship_status": NONE,
        "request_id": request_id,
    }


async def unfriend(user: dict, other: int) -> dict:
    me = user["id"]
    async with transaction() as conn:
        username: Optional[str] = await conn.fetchval("SELECT username FROM users WHERE id = $1", other)
        result = await conn.execute(
            """
            DELETE FROM friendships
            WHERE (user_id = $1 AND friend_id = $2) OR (user_id = $2 AND friend_id = $1)
            """,
            me, other,
        )
    if int(result.split()[-1]) == 0:
        raise HTTPException(status_code=404, detail="You are not friends with this runner")
    return {"message": f"You are no longer friends with @{username}", "friendship_status": NONE, "request_id": None}
