import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
import jwt
from pwdlib import PasswordHash
from pyproj import Geod
from shapely.geometry import Polygon
from shapely.validation import make_valid

from db.db_conn import (
    insert_records,
    insert_record_get_id,
    insert_many_records,
    fetch_record,
    execute_query,
    execute_query_one,
    sqlite_connection,
)
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

router = APIRouter(
    prefix="/api/v1/bases",
    tags=["bases"]
)

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "default-dev-secret-key-change-in-production")
ALGORITHM = "HS256"
# Configurable token expiration (default: 1440 minutes = 24 hours / 1 day)
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "1440"))

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/bases/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/bases/login", auto_error=False)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Creates signed cryptographic JWT with random session identifier (jti).
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    to_encode.update({"exp": expire})
    if "iat" not in to_encode:
        to_encode["iat"] = int(datetime.now(timezone.utc).timestamp())
    if "jti" not in to_encode:
        to_encode["jti"] = secrets.token_hex(32)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Enforces server-time token expiry and verifies session state against server-side storage.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        session_id: str | None = payload.get("jti")
        if user_id is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    conn = await sqlite_connection()
    try:
        # Check server-side session revocation
        if session_id:
            session_row = await execute_query_one(
                conn,
                "SELECT session_id, user_id, expires_at, is_revoked FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            if session_row and session_row["is_revoked"] == 1:
                raise HTTPException(
                    status_code=401,
                    detail="Session has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        user = await fetch_record(conn, "users", "id", (int(user_id),))
        if not user:
            raise HTTPException(
                status_code=404,
                detail=Message.USER_NOT_FOUND,
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_dict = dict(user)
        user_dict["current_session_id"] = session_id
        return user_dict
    finally:
        await conn.close()


async def get_current_user_optional(token: str | None = Depends(oauth2_scheme_optional)) -> dict | None:
    """
    Optional authentication dependency: verifies session if token is provided, returns None if not.
    """
    if not token:
        return None
    try:
        return await get_current_user(token)
    except HTTPException:
        raise
    except Exception:
        return None


@router.post("/register", response_model=RegisterResponse)
async def register_user(request: RegisterRequest):
    conn = await sqlite_connection()
    try:
        existing_username = await fetch_record(conn, "users", "username", (request.username,))
        if existing_username:
            raise HTTPException(status_code=400, detail=Message.ALREADY_EXISTS)

        existing_email = await fetch_record(conn, "users", "email", (request.email,))
        if existing_email:
            raise HTTPException(status_code=400, detail=Message.ALREADY_EXISTS)

        hashed_password = password_hash.hash(request.password)
        first_name = request.first_name or "Runner"
        last_name = request.last_name or ""
        phone = request.phone or ""

        user_id = await insert_record_get_id(
            conn,
            "users",
            ["username", "hashed_password", "first_name", "last_name", "email", "phone"],
            [request.username, hashed_password, first_name, last_name, request.email, phone],
        )

        session_id = secrets.token_hex(32)
        expires_delta = timedelta(minutes=ACCESS_TOKEN_MINUTES)
        expire = datetime.now(timezone.utc) + expires_delta

        token = create_access_token(
            data={"sub": str(user_id), "jti": session_id},
            expires_delta=expires_delta,
        )

        # Store session in server-side database
        await conn.execute(
            "INSERT INTO sessions (session_id, user_id, expires_at, is_revoked) VALUES (?, ?, ?, 0)",
            (session_id, user_id, expire.isoformat()),
        )
        await conn.commit()

        return {
            "message": Message.USER_CREATED,
            "token": token,
            "access_token": token,
            "user_id": str(user_id),
            "expires_in": ACCESS_TOKEN_MINUTES * 60,
            "expires_at": expire.isoformat(),
            "session_id": session_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error during registration: {e}")
        raise HTTPException(status_code=500, detail=Message.INTERNAL_ERROR)
    finally:
        await conn.close()


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    conn = await sqlite_connection()
    try:
        user = None
        identifier = request.username or request.email
        if identifier:
            user = await fetch_record(conn, "users", "username", (identifier,))
            if not user:
                user = await fetch_record(conn, "users", "email", (identifier,))

        if not user:
            raise HTTPException(status_code=404, detail=Message.USER_NOT_FOUND)

        if not password_hash.verify(request.password, user["hashed_password"]):
            raise HTTPException(status_code=401, detail=Message.INVALID_USERNAME_OR_PASSWORD)

        session_id = secrets.token_hex(32)
        expires_delta = timedelta(minutes=ACCESS_TOKEN_MINUTES)
        expire = datetime.now(timezone.utc) + expires_delta

        token = create_access_token(
            data={"sub": str(user["id"]), "jti": session_id},
            expires_delta=expires_delta,
        )

        # Invalidate old sessions for this user on each new login
        await conn.execute(
            "UPDATE sessions SET is_revoked = 1 WHERE user_id = ? AND is_revoked = 0",
            (user["id"],),
        )

        # Record new session in server-side database
        await conn.execute(
            "INSERT INTO sessions (session_id, user_id, expires_at, is_revoked) VALUES (?, ?, ?, 0)",
            (session_id, user["id"], expire.isoformat()),
        )
        await conn.commit()

        return {
            "access_token": token,
            "token": token,
            "token_type": "bearer",
            "message": Message.LOGIN_SUCCESSFUL,
            "expires_in": ACCESS_TOKEN_MINUTES * 60,
            "expires_at": expire.isoformat(),
            "session_id": session_id,
        }
    except HTTPException:
        raise

    except Exception as e:
        print(f"Error during login: {e}")
        raise HTTPException(status_code=500, detail=Message.INTERNAL_ERROR)
    finally:
        await conn.close()


@router.post("/logout", response_model=LogoutResponse)
async def logout(current_user: dict = Depends(get_current_user)):
    """
    Invalidates current session on the server.
    """
    session_id = current_user.get("current_session_id")
    if session_id:
        conn = await sqlite_connection()
        try:
            await conn.execute(
                "UPDATE sessions SET is_revoked = 1 WHERE session_id = ?",
                (session_id,),
            )
            await conn.commit()
        finally:
            await conn.close()
    return {"status": "success", "message": "Successfully logged out"}


@router.get("/me", response_model=UserProfile)
async def get_me(user: dict = Depends(get_current_user)):
    return user



@router.post("/telemetry", response_model=TelemetryResponse)
async def record_telemetry(request: LocationBatch):
    valid_points_data = []

    for point in request.points:
        if point.accuracy and point.accuracy > 20.0:
            continue
        if point.speed and point.speed > 27.78:  # approx 100km/h
            continue

        ts_str = point.timestamp.isoformat() if hasattr(point.timestamp, "isoformat") else str(point.timestamp)
        valid_points_data.append([
            str(request.activity_id),
            str(request.user_id),
            point.latitude,
            point.longitude,
            point.altitude,
            point.speed,
            point.accuracy,
            ts_str,
        ])

    if valid_points_data:
        conn = await sqlite_connection()
        try:
            await insert_many_records(
                conn,
                "telemetry_points",
                ["activity_id", "user_id", "latitude", "longitude", "altitude", "speed", "accuracy", "timestamp"],
                valid_points_data,
            )
        finally:
            await conn.close()

    return {
        "status": "success",
        "message": "telemetry recorded",
        "saved_points": len(valid_points_data),
        "total_received": len(request.points),
    }


@router.post("/claim", response_model=ClaimBaseResponse)
async def claim_base(request: RunPath):
    if len(request.points) < 5:
        raise HTTPException(status_code=400, detail="not enough points to claim an area")

    coords = [(p.longitude, p.latitude) for p in request.points]

    try:
        base_polygon = make_valid(Polygon(coords))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid path geometry: {str(e)}")

    if not base_polygon.is_valid:
        base_polygon = make_valid(base_polygon)

    # Use pyproj because standard Euclidean math fails on round earth
    geod = Geod(ellps='WGS84')

    try:
        area, perimeter = geod.geometry_area_perimeter(base_polygon)
        area_sq_meters = abs(area)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to calculate geometry: {str(e)}")

    # Anti-cheat
    if area_sq_meters < 100:
        return {"status": "failed", "message": "Base is too small", "area": round(area_sq_meters, 2)}

    if area_sq_meters > 500000:
        return {"status": "failed", "message": "Base is too large", "area": round(area_sq_meters, 2)}

    # Save claimed base polygon to database
    conn = await sqlite_connection()
    base_id = None
    try:
        base_id = await insert_record_get_id(
            conn,
            "claimed_bases",
            ["user_id", "status", "area", "perimeter", "polygon_wkt"],
            [
                str(request.user_id),
                "active",
                round(area_sq_meters, 2),
                round(perimeter, 2),
                base_polygon.wkt,
            ],
        )
    finally:
        await conn.close()

    return {
        "status": "success",
        "message": "Area claimed successfully",
        "base_id": base_id,
        "area": round(area_sq_meters, 2),
        "perimeter": round(perimeter, 2),
        "polygon_wkt": base_polygon.wkt,
    }


# ==========================================
# UI GET Endpoints for Map & Dashboard
# ==========================================

@router.get("", response_model=BaseListResponse)
async def get_bases(
    user_id: str | None = Query(None, description="Filter bases by user ID (optional)"),
    limit: int = Query(50, ge=1, le=200, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
):
    """
    Get claimed bases for map rendering.
    Returns all bases or filters by user_id with pagination.
    """
    conn = await sqlite_connection()
    try:
        if user_id:
            query = "SELECT * FROM claimed_bases WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?"
            count_query = "SELECT count(*) as cnt FROM claimed_bases WHERE user_id = ?"
            rows = await execute_query(conn, query, (user_id, limit, offset))
            count_row = await execute_query_one(conn, count_query, (user_id,))
        else:
            query = "SELECT * FROM claimed_bases ORDER BY id DESC LIMIT ? OFFSET ?"
            count_query = "SELECT count(*) as cnt FROM claimed_bases"
            rows = await execute_query(conn, query, (limit, offset))
            count_row = await execute_query_one(conn, count_query)

        total = count_row["cnt"] if count_row else 0
        return {
            "total": total,
            "bases": [dict(r) for r in rows],
        }
    finally:
        await conn.close()


@router.get("/user/{user_id}/stats", response_model=UserStatsResponse)
async def get_user_stats(user_id: str):
    """
    Get aggregated territory stats for a user (total bases, total area, total perimeter).
    """
    conn = await sqlite_connection()
    try:
        row = await execute_query_one(
            conn,
            """
            SELECT 
                count(*) as total_bases,
                COALESCE(SUM(area), 0.0) as total_area_sq_meters,
                COALESCE(SUM(perimeter), 0.0) as total_perimeter_meters
            FROM claimed_bases 
            WHERE user_id = ? AND status = 'active'
            """,
            (user_id,),
        )
        return {
            "user_id": user_id,
            "total_bases": row["total_bases"] if row else 0,
            "total_area_sq_meters": round(row["total_area_sq_meters"], 2) if row else 0.0,
            "total_perimeter_meters": round(row["total_perimeter_meters"], 2) if row else 0.0,
        }
    finally:
        await conn.close()


@router.get("/telemetry/{activity_id}", response_model=ActivityTelemetryResponse)
async def get_activity_telemetry(activity_id: str):
    """
    Get all recorded GPS breadcrumb points for an activity to draw the runner's path on the map.
    """
    conn = await sqlite_connection()
    try:
        rows = await execute_query(
            conn,
            "SELECT latitude, longitude, altitude, speed, accuracy, timestamp, user_id FROM telemetry_points WHERE activity_id = ? ORDER BY id ASC",
            (activity_id,),
        )
        if not rows:
            raise HTTPException(status_code=404, detail="No telemetry found for this activity")

        return {
            "activity_id": activity_id,
            "user_id": rows[0]["user_id"],
            "total_points": len(rows),
            "points": [
                {
                    "latitude": r["latitude"],
                    "longitude": r["longitude"],
                    "altitude": r["altitude"],
                    "speed": r["speed"],
                    "accuracy": r["accuracy"],
                    "timestamp": r["timestamp"],
                }
                for r in rows
            ],
        }
    finally:
        await conn.close()


@router.get("/{base_id}", response_model=BaseItem)
async def get_base_by_id(base_id: int):
    """
    Get detailed information about a single claimed base by its ID.
    """
    conn = await sqlite_connection()
    try:
        row = await execute_query_one(conn, "SELECT * FROM claimed_bases WHERE id = ?", (base_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Base not found")
        return dict(row)
    finally:
        await conn.close()