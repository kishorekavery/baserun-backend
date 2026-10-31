import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash

from db.database import connection
from models.datamodel import (
    ActivityTelemetryResponse,
    BaseItem,
    BaseListResponse,
    ClaimBaseResponse,
    LocationBatch,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    Message,
    RegisterRequest,
    RegisterResponse,
    RunPath,
    TelemetryResponse,
    UserProfile,
    UserStatsResponse,
)
from services import privacy, run_service, territory_service
from services.ownership import get_activity_with_ownership, verify_ownership  # noqa: F401 (re-exported)
from services.territory_service import TERRITORY_SELECT, base_item
from utils.helper import iso, utcnow

logger = logging.getLogger("baserun.auth")

router = APIRouter(
    prefix="/api/v1/bases",
    tags=["bases"]
)

DEFAULT_SECRET_KEY = "default-dev-secret-key-change-in-production"
SECRET_KEY = os.getenv("JWT_SECRET_KEY", DEFAULT_SECRET_KEY)
if SECRET_KEY == DEFAULT_SECRET_KEY:
    logger.warning("JWT_SECRET_KEY is not set; using an insecure development key")
ALGORITHM = "HS256"
# Session settings with production-safe defaults
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
SESSION_MODE = os.getenv("SESSION_MODE", "idle").strip().lower()
MAX_SESSIONS_PER_USER = int(os.getenv("MAX_SESSIONS_PER_USER", "1"))
SESSION_ABSOLUTE_MAX_SECONDS = int(os.getenv("SESSION_ABSOLUTE_MAX_SECONDS", "2592000"))
SESSION_TOUCH_INTERVAL_SECONDS = 300

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/bases/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/bases/login", auto_error=False)

USER_PROFILE_SQL = """
    SELECT u.*, c.name AS club_name
    FROM users u
    LEFT JOIN clubs c ON c.id = u.club_id
"""


def _setting(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


async def hash_password(password: str) -> str:
    # Argon2 is CPU bound; keep it off the event loop
    return await asyncio.to_thread(password_hash.hash, password)


async def verify_password(password: str, hashed: str) -> bool:
    try:
        return await asyncio.to_thread(password_hash.verify, password, hashed)
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Creates signed cryptographic JWT with random session identifier (jti).
    Default JWT expiration is hard upper limit SESSION_ABSOLUTE_MAX_SECONDS (30 days).
    """
    to_encode = data.copy()
    if expires_delta is None:
        expires_delta = timedelta(seconds=_setting("SESSION_ABSOLUTE_MAX_SECONDS", SESSION_ABSOLUTE_MAX_SECONDS))
    now = datetime.now(timezone.utc)
    to_encode["exp"] = now + expires_delta
    to_encode.setdefault("iat", int(now.timestamp()))
    to_encode.setdefault("jti", secrets.token_hex(32))
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def create_user_session(conn: asyncpg.Connection, user_id: int) -> tuple[str, str, int, str]:
    """
    Creates a new session for user_id, revoking the oldest active sessions so
    that at most MAX_SESSIONS_PER_USER remain active.
    Returns (session_id, token, expires_in, expires_at_iso).
    """
    max_sessions = max(_setting("MAX_SESSIONS_PER_USER", MAX_SESSIONS_PER_USER), 1)
    ttl_seconds = _setting("SESSION_TTL_SECONDS", SESSION_TTL_SECONDS)
    abs_max_seconds = _setting("SESSION_ABSOLUTE_MAX_SECONDS", SESSION_ABSOLUTE_MAX_SECONDS)
    now = utcnow()

    async with conn.transaction():
        # Serialise concurrent logins of the same user
        await conn.execute("SELECT id FROM users WHERE id = $1 FOR UPDATE", user_id)
        await conn.execute(
            "DELETE FROM sessions WHERE user_id = $1 AND expires_at < $2",
            user_id, now - timedelta(days=1),
        )
        active = await conn.fetch(
            """
            SELECT id FROM sessions
            WHERE user_id = $1 AND is_revoked = FALSE AND expires_at > $2
            ORDER BY id DESC
            """,
            user_id, now,
        )
        to_revoke = [row["id"] for row in active[max_sessions - 1:]]
        if to_revoke:
            await conn.execute("UPDATE sessions SET is_revoked = TRUE WHERE id = ANY($1::bigint[])", to_revoke)

        session_id = secrets.token_hex(32)
        expires_at = now + timedelta(seconds=min(ttl_seconds, abs_max_seconds))
        await conn.execute(
            """
            INSERT INTO sessions (session_id, user_id, created_at, expires_at, last_seen_at, is_revoked)
            VALUES ($1, $2, $3, $4, $3, FALSE)
            """,
            session_id, user_id, now, expires_at,
        )

    token = create_access_token(
        data={"sub": str(user_id), "jti": session_id},
        expires_delta=timedelta(seconds=abs_max_seconds),
    )
    return session_id, token, ttl_seconds, iso(expires_at)


async def build_auth_response(conn: asyncpg.Connection, user_id: int, message: Message, **extra) -> dict:
    session_id, token, expires_in, expires_at = await create_user_session(conn, user_id)
    user = await conn.fetchrow(USER_PROFILE_SQL + " WHERE u.id = $1", user_id)
    return {
        "access_token": token,
        "token": token,
        "token_type": "bearer",
        "message": message,
        "expires_in": expires_in,
        "expires_at": expires_at,
        "session_id": session_id,
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "territory_color": user["territory_color"],
        "avatar_url": user["avatar_url"],
        "club_id": user["club_id"],
        "club_name": user["club_name"],
        **extra,
    }


def _decode_token(token: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise _unauthorized("Invalid authentication credentials")
    user_id = payload.get("sub")
    session_id = payload.get("jti")
    if user_id is None or not str(user_id).isdigit():
        raise _unauthorized("Invalid authentication credentials")
    if not session_id:
        raise _unauthorized("Token missing session identifier (jti)")
    return int(user_id), str(session_id)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Validates the JWT, then the server-side session (revocation, expiry) and
    slides the expiry forward in idle mode (throttled to every 5 minutes).
    """
    user_id, session_id = _decode_token(token)
    now = utcnow()

    async with connection() as conn:
        session = await conn.fetchrow(
            "SELECT user_id, created_at, expires_at, last_seen_at, is_revoked FROM sessions WHERE session_id = $1",
            session_id,
        )
        if not session or session["user_id"] != user_id:
            raise _unauthorized("Session not found or invalid")
        if session["is_revoked"]:
            raise _unauthorized("Session has been revoked")
        if session["expires_at"] <= now:
            raise _unauthorized("Session expired")

        mode = os.getenv("SESSION_MODE", SESSION_MODE).strip().lower()
        if mode == "idle" and (now - session["last_seen_at"]).total_seconds() > SESSION_TOUCH_INTERVAL_SECONDS:
            ttl = _setting("SESSION_TTL_SECONDS", SESSION_TTL_SECONDS)
            abs_max = _setting("SESSION_ABSOLUTE_MAX_SECONDS", SESSION_ABSOLUTE_MAX_SECONDS)
            new_expires = min(now + timedelta(seconds=ttl), session["created_at"] + timedelta(seconds=abs_max))
            await conn.execute(
                "UPDATE sessions SET last_seen_at = $1, expires_at = GREATEST(expires_at, $2) WHERE session_id = $3",
                now, new_expires, session_id,
            )

        user = await conn.fetchrow(USER_PROFILE_SQL + " WHERE u.id = $1", user_id)

    if not user or not user["is_active"]:
        raise _unauthorized("User not found or inactive")
    user_dict = dict(user)
    user_dict.pop("hashed_password", None)
    user_dict["current_session_id"] = session_id
    return user_dict


async def get_current_user_optional(token: str | None = Depends(oauth2_scheme_optional)) -> dict | None:
    """
    Optional authentication for public endpoints: a missing, expired or
    revoked token is treated as an anonymous viewer.
    """
    if not token:
        return None
    try:
        return await get_current_user(token)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


@router.post("/register", response_model=RegisterResponse)
async def register_user(request: RegisterRequest):
    hashed = await hash_password(request.password)
    async with connection() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM users WHERE LOWER(username) = LOWER($1) OR LOWER(email) = LOWER($2))",
            request.username, request.email,
        )
        if exists:
            raise HTTPException(status_code=400, detail=Message.ALREADY_EXISTS)
        try:
            user_id = await conn.fetchval(
                """
                INSERT INTO users (username, email, hashed_password, first_name, last_name, phone)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                request.username,
                request.email.lower(),
                hashed,
                (request.first_name or "Runner").strip() or "Runner",
                (request.last_name or "").strip(),
                (request.phone or "").strip(),
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=400, detail=Message.ALREADY_EXISTS)
        return await build_auth_response(conn, user_id, Message.USER_CREATED)


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    identifier = request.username or request.email
    async with connection() as conn:
        user = await conn.fetchrow(
            """
            SELECT id, hashed_password, is_active FROM users
            WHERE LOWER(username) = LOWER($1) OR LOWER(email) = LOWER($1)
            ORDER BY (LOWER(username) = LOWER($1)) DESC
            LIMIT 1
            """,
            identifier,
        )
        if not user or not user["is_active"] or not await verify_password(request.password, user["hashed_password"]):
            raise HTTPException(status_code=401, detail=Message.INVALID_USERNAME_OR_PASSWORD)
        return await build_auth_response(conn, user["id"], Message.LOGIN_SUCCESSFUL)


@router.post("/logout", response_model=LogoutResponse)
async def logout(current_user: dict = Depends(get_current_user)):
    """
    Invalidates current session on the server.
    """
    async with connection() as conn:
        await conn.execute(
            "UPDATE sessions SET is_revoked = TRUE WHERE session_id = $1",
            current_user["current_session_id"],
        )
    return {"status": "success", "message": "Successfully logged out"}


@router.get("/me", response_model=UserProfile)
async def get_me(user: dict = Depends(get_current_user)):
    return {**user, "created_at": iso(user["created_at"])}


@router.post("/telemetry", response_model=TelemetryResponse)
async def record_telemetry(
    request: LocationBatch,
    current_user: dict = Depends(get_current_user),
):
    """
    Legacy telemetry ingestion. Points are attached to `activity_id`
    (created on the fly when unknown, or minted when omitted).
    """
    activity_id = request.activity_id or f"act_{secrets.token_hex(12)}"
    return await run_service.ingest_telemetry(current_user, activity_id, request)


@router.post("/claim", response_model=ClaimBaseResponse)
async def claim_base(
    request: RunPath,
    current_user: dict = Depends(get_current_user),
):
    """
    Legacy direct polygon claim (no run attached).
    """
    return await territory_service.claim_raw_polygon(current_user, request.points)


# ==========================================
# UI GET Endpoints for Map & Dashboard
# ==========================================

@router.get("", response_model=BaseListResponse)
async def get_bases(
    user_id: int | None = Query(None, description="Filter bases by user ID (optional)"),
    limit: int = Query(50, ge=1, le=200, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    current_user: dict | None = Depends(get_current_user_optional),
):
    """
    Get claimed bases for map rendering, optionally filtered by owner.
    """
    viewer_id = current_user["id"] if current_user else None
    async with connection() as conn:
        if user_id is not None:
            await privacy.require_visible(conn, user_id, viewer_id)
        rows = await conn.fetch(
            TERRITORY_SELECT + """
            WHERE t.status = 'active' AND ($1::int IS NULL OR t.owner_id = $1)
            ORDER BY t.id DESC LIMIT $2 OFFSET $3
            """,
            user_id, limit, offset,
        )
        total = await conn.fetchval(
            "SELECT count(*) FROM territories WHERE status = 'active' AND ($1::int IS NULL OR owner_id = $1)",
            user_id,
        )
        rows = await privacy.mask_territories(conn, rows, viewer_id)
    return {"total": total, "bases": [base_item(r) for r in rows]}


@router.get("/user/{user_id}/stats", response_model=UserStatsResponse)
async def get_user_stats(user_id: int, current_user: dict | None = Depends(get_current_user_optional)):
    """
    Aggregated territory stats for a user (total bases, total area, total perimeter).
    """
    async with connection() as conn:
        await privacy.require_visible(conn, user_id, current_user["id"] if current_user else None)
        row = await conn.fetchrow(
            """
            SELECT count(*) AS total_bases,
                   COALESCE(SUM(area_km2), 0) AS area_km2,
                   COALESCE(SUM(perimeter_km), 0) AS perimeter_km
            FROM territories
            WHERE owner_id = $1 AND status = 'active'
            """,
            user_id,
        )
    return {
        "user_id": str(user_id),
        "total_bases": row["total_bases"],
        "total_area_sq_meters": round(row["area_km2"] * 1_000_000, 2),
        "total_perimeter_meters": round(row["perimeter_km"] * 1000, 2),
    }


@router.get("/telemetry/{activity_id}", response_model=ActivityTelemetryResponse)
async def get_activity_telemetry(activity_id: str, current_user: dict = Depends(get_current_user)):
    """
    All recorded GPS breadcrumbs of one of the caller's runs.
    """
    async with connection() as conn:
        run = await get_activity_with_ownership(conn, activity_id, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT latitude, longitude, altitude, speed, accuracy, recorded_at
            FROM telemetry_points WHERE run_id = $1 ORDER BY id
            """,
            run["id"],
        )
    if not rows:
        raise HTTPException(status_code=404, detail="No telemetry found for this activity")
    return {
        "activity_id": activity_id,
        "user_id": str(run["user_id"]),
        "total_points": len(rows),
        "points": [{**dict(r), "timestamp": iso(r["recorded_at"])} for r in rows],
    }


@router.get("/{base_id}", response_model=BaseItem)
async def get_base_by_id(base_id: int, current_user: dict | None = Depends(get_current_user_optional)):
    """
    Get detailed information about a single claimed base by its ID.
    """
    async with connection() as conn:
        row = await conn.fetchrow(TERRITORY_SELECT + " WHERE t.id = $1", base_id)
        if not row:
            raise HTTPException(status_code=404, detail="Base not found")
        masked = await privacy.mask_territories(conn, [row], current_user["id"] if current_user else None)
    return base_item(masked[0])
