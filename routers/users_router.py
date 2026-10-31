from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from db.database import connection, transaction
from models.datamodel import (
    AccountActionResponse,
    AchievementsResponse,
    BaseListResponse,
    ConnectAppRequest,
    ConnectedAppsResponse,
    DeleteAccountRequest,
    FullUserProfileResponse,
    IdentityResponse,
    RunHistoryResponse,
    UpdateIdentityRequest,
)
from routers.core import USER_PROFILE_SQL, get_current_user, get_current_user_optional, verify_ownership, verify_password
from services import account_service, avatar_service, privacy
from services.club_service import set_user_club
from services.colors import is_premium_color
from services.territory_service import TERRITORY_SELECT, base_item
from utils.helper import format_duration, format_pace, iso, pace_seconds_per_km
from utils.social_auth import SocialAuthError, resolve_identity

router = APIRouter(prefix="/users", tags=["users"])
avatar_images_router = APIRouter(prefix="/api/v1/avatars", tags=["users"])

SOCIAL_PROVIDERS = {"google": "Google", "apple": "Apple"}


def _restricted_profile(user) -> dict:
    """The card a viewer gets when the runner's privacy level hides everything else."""
    return {
        "id": user["id"],
        "username": user["username"],
        "first_name": user["username"],
        "avatar_url": user["avatar_url"] or "",
        "avatar_thumb_url": user["avatar_thumb_url"] or "",
        "city": "",
        "territory_color": user["territory_color"],
        "total_territories": 0,
        "total_km": 0,
        "total_runs": 0,
        "stats": {
            "total_runs": 0,
            "total_km": 0,
            "total_duration_hours": 0,
            "bases_claimed": 0,
            "bases_held": 0,
            "avg_pace": format_pace(0),
            "avg_pace_seconds": 0,
        },
        "is_restricted": True,
    }


async def _build_full_profile(user_id: int, viewer_id: Optional[int]) -> dict:
    is_self = viewer_id == user_id
    async with connection() as conn:
        user = await conn.fetchrow(USER_PROFILE_SQL + " WHERE u.id = $1 AND u.is_active", user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not await privacy.is_visible(conn, user_id, viewer_id):
            return _restricted_profile(user)
        runs = await conn.fetchrow(
            """
            SELECT count(*) AS total_runs,
                   COALESCE(SUM(distance_km), 0) AS total_km,
                   COALESCE(SUM(duration_seconds), 0) AS total_seconds,
                   count(*) FILTER (WHERE claim_outcome IN ('success', 'takeover')) AS bases_claimed
            FROM runs WHERE user_id = $1 AND status = 'completed'
            """,
            user_id,
        )
        bases_held = await conn.fetchval(
            "SELECT count(*) FROM territories WHERE owner_id = $1 AND status = 'active'", user_id
        )
        favorite = await conn.fetchval(
            """
            SELECT name FROM territories WHERE owner_id = $1 AND status = 'active'
            ORDER BY total_runs DESC, area_km2 DESC LIMIT 1
            """,
            user_id,
        )

    total_km = round(runs["total_km"], 1)
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"] if is_self else None,
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "avatar_url": user["avatar_url"] or "",
        "avatar_thumb_url": user["avatar_thumb_url"] or "",
        "bio": user["bio"],
        "city": user["city"] or "",
        "territory_color": user["territory_color"],
        "total_territories": bases_held,
        "total_km": total_km,
        "total_runs": runs["total_runs"],
        "club_id": user["club_id"],
        "club_name": user["club_name"],
        "favorite_base": favorite,
        "stats": {
            "total_runs": runs["total_runs"],
            "total_km": total_km,
            "total_duration_hours": round(runs["total_seconds"] / 3600, 1),
            "bases_claimed": runs["bases_claimed"],
            "bases_held": bases_held,
            "avg_pace": format_pace(pace_seconds_per_km(runs["total_seconds"], runs["total_km"])),
            "avg_pace_seconds": pace_seconds_per_km(runs["total_seconds"], runs["total_km"]),
        },
        "privacy_level": user["privacy_level"] if is_self else None,
        "auth_provider": user["auth_provider"] if is_self else None,
        "is_premium": user["is_premium"] if is_self else None,
    }


async def _identity_response(user_id: int, message: str) -> dict:
    async with connection() as conn:
        user = await conn.fetchrow(USER_PROFILE_SQL + " WHERE u.id = $1", user_id)
    return {
        "user_id": str(user["id"]),
        "username": user["username"],
        "territory_color": user["territory_color"],
        "avatar_url": user["avatar_url"] or "",
        "avatar_thumb_url": user["avatar_thumb_url"] or "",
        "bio": user["bio"],
        "club_id": user["club_id"],
        "club_name": user["club_name"],
        "privacy_level": user["privacy_level"],
        "message": message,
    }


async def _update_identity(user: dict, request: UpdateIdentityRequest) -> dict:
    color = request.territory_color
    # A runner who already holds a premium color keeps it
    if color and color != user["territory_color"] and is_premium_color(color) and not user["is_premium"]:
        raise HTTPException(status_code=403, detail="That is a premium color")

    fields = request.model_fields_set
    updates = {}
    if request.username is not None and request.username != user["username"]:
        updates["username"] = request.username
    for column in ("territory_color", "avatar_url", "bio", "first_name", "last_name", "city", "privacy_level"):
        value = getattr(request, column)
        if value is not None:
            updates[column] = value.strip() if isinstance(value, str) else value
    replaces_photo = "avatar_url" in updates and updates["avatar_url"] != user["avatar_url"]
    if replaces_photo:
        updates["avatar_thumb_url"] = None

    async with transaction() as conn:
        if updates:
            assignments = ", ".join(f"{column} = ${i}" for i, column in enumerate(updates, start=2))
            try:
                await conn.execute(f"UPDATE users SET {assignments} WHERE id = $1", user["id"], *updates.values())
            except asyncpg.UniqueViolationError:
                raise HTTPException(status_code=400, detail="Username is already taken")
        if replaces_photo:
            await conn.execute("DELETE FROM avatars WHERE user_id = $1", user["id"])  # replaced by a plain URL
        # club_id: a value joins that club, an explicit null leaves the current club
        if "club_id" in fields and request.club_id != user["club_id"]:
            await set_user_club(conn, user["id"], request.club_id)

    return await _identity_response(user["id"], "Identity updated successfully")


@router.get("/me", response_model=FullUserProfileResponse)
async def get_my_profile_me(current_user: dict = Depends(get_current_user)):
    """
    Screen 12: the authenticated runner's profile, lifetime stats and club.
    """
    return await _build_full_profile(current_user["id"], current_user["id"])


@router.get("/profile", response_model=FullUserProfileResponse)
async def get_my_full_profile(current_user: dict = Depends(get_current_user)):
    """
    Screen 12: alias of /users/me.
    """
    return await _build_full_profile(current_user["id"], current_user["id"])


@router.post(
    "/me/avatar",
    response_model=IdentityResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"multipart/form-data": {"schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }}},
        },
    },
)
async def upload_avatar(request: Request, current_user: dict = Depends(get_current_user)):
    """
    Edit Profile: upload or replace the profile photo (JPEG, PNG or WebP, 5 MB max).
    The body is read here rather than declared as a parameter so that it is
    only accepted after authentication, and is cut off at the size limit.
    """
    await avatar_service.upload_avatar(request, current_user["id"])
    return await _identity_response(current_user["id"], "Profile photo updated")


@router.delete("/me/avatar", response_model=IdentityResponse)
async def remove_avatar(current_user: dict = Depends(get_current_user)):
    """
    Edit Profile: remove the profile photo. The app falls back to initials.
    """
    await avatar_service.remove_avatar(current_user["id"])
    return await _identity_response(current_user["id"], "Profile photo removed")


def _image_response(data: Optional[bytes]) -> Response:
    if data is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": avatar_service.CACHE_CONTROL, "X-Content-Type-Options": "nosniff"},
    )


@avatar_images_router.get("/{key}_128.jpg")
async def get_avatar_thumbnail(key: str):
    """
    Public image bytes behind avatar_thumb_url. Registered first: `{key}` would also match "abc_128".
    """
    return _image_response(await avatar_service.get_avatar_image(key, thumbnail=True))


@avatar_images_router.get("/{key}.jpg")
async def get_avatar_image(key: str):
    """
    Public image bytes behind avatar_url. Keys are random and change on every upload.
    """
    return _image_response(await avatar_service.get_avatar_image(key))


async def _connected_apps(user: dict) -> dict:
    async with connection() as conn:
        rows = await conn.fetch(
            "SELECT provider, connected_at FROM connected_accounts WHERE user_id = $1 AND disconnected_at IS NULL",
            user["id"],
        )
    connected_at = {r["provider"]: r["connected_at"] for r in rows}
    primary = user["auth_provider"]
    apps = []
    for provider, name in SOCIAL_PROVIDERS.items():
        # Accounts created before connected_accounts existed have no row
        connected = provider in connected_at or provider == primary
        apps.append({
            "provider": provider,
            "name": name,
            "connected": connected,
            "connected_at": iso(connected_at.get(provider)),
            "is_primary": provider == primary,
            "can_disconnect": connected and provider != primary,
        })
    return {"apps": apps}


def _provider_name(provider: str) -> str:
    if provider not in SOCIAL_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return SOCIAL_PROVIDERS[provider]


async def _require_provider_proof(user: dict, provider: str, token: Optional[str], email: Optional[str]) -> None:
    """
    Proves the caller controls the provider account, not just this session.
    Failures are 400, not 401: the app logs the runner out on a 401.
    """
    name = _provider_name(provider)
    if not token:
        raise HTTPException(status_code=400, detail=f"Sign in with {name} again to confirm")
    try:
        identity = await resolve_identity(provider, token, email)
    except SocialAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if identity.email != user["email"].lower():
        raise HTTPException(status_code=400, detail=f"That {name} account does not match this profile")


@router.get("/me/connected-apps", response_model=ConnectedAppsResponse)
async def get_connected_apps(current_user: dict = Depends(get_current_user)):
    """
    Edit Profile: the social sign-ins connected to this account.
    """
    return await _connected_apps(current_user)


@router.post("/me/connected-apps/{provider}", response_model=ConnectedAppsResponse)
async def connect_app(provider: str, request: ConnectAppRequest, current_user: dict = Depends(get_current_user)):
    """
    Edit Profile: connect a provider, or reconnect one disconnected earlier,
    using a fresh ID token for the same email as this account.
    """
    await _require_provider_proof(current_user, provider, request.token, request.email)
    async with connection() as conn:
        await conn.execute(
            """
            INSERT INTO connected_accounts (user_id, provider) VALUES ($1, $2)
            ON CONFLICT (user_id, provider) DO UPDATE SET disconnected_at = NULL, connected_at = NOW()
            """,
            current_user["id"], provider,
        )
    return await _connected_apps(current_user)


@router.delete("/me/connected-apps/{provider}", response_model=ConnectedAppsResponse)
async def disconnect_app(provider: str, current_user: dict = Depends(get_current_user)):
    """
    Edit Profile: stop a provider signing in to this account. The provider the
    account was created with can't be removed: nothing else could sign in.
    """
    name = _provider_name(provider)
    if provider == current_user["auth_provider"]:
        raise HTTPException(status_code=400, detail=f"{name} is how you sign in, so it can't be disconnected")
    async with connection() as conn:
        result = await conn.execute(
            """
            UPDATE connected_accounts SET disconnected_at = NOW()
            WHERE user_id = $1 AND provider = $2 AND disconnected_at IS NULL
            """,
            current_user["id"], provider,
        )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail=f"{name} is not connected")
    return await _connected_apps(current_user)


@router.post("/me/delete", response_model=AccountActionResponse)
async def delete_my_account(request: DeleteAccountRequest, current_user: dict = Depends(get_current_user)):
    """
    Edit Profile: permanently delete the account and everything recorded for it.
    A POST because the app's DELETE helper sends no body, and this needs the
    password. Failures are 400, not 401: the app logs the runner out on a 401.
    """
    if current_user["auth_provider"] == "password":
        async with connection() as conn:
            hashed = await conn.fetchval("SELECT hashed_password FROM users WHERE id = $1", current_user["id"])
        if not request.password or not await verify_password(request.password, hashed):
            raise HTTPException(status_code=400, detail="Password is incorrect")
    else:
        await _require_provider_proof(current_user, current_user["auth_provider"], request.token, request.email)

    await account_service.delete_account(current_user["id"])
    return {"status": "success", "message": "Account deleted"}


@router.put("/identity", response_model=IdentityResponse)
async def update_identity(
    request: UpdateIdentityRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 4: choose username, territory color (#RRGGBB), avatar, bio and club.
    Territory colors are joined from the owner at read time, so a color
    change repaints all of the runner's bases immediately.
    """
    return await _update_identity(current_user, request)


@router.put("/{user_id}", response_model=IdentityResponse)
async def update_user(
    user_id: int,
    request: UpdateIdentityRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 4: same as PUT /users/identity, addressed by id (own account only).
    """
    verify_ownership(user_id, current_user["id"])
    return await _update_identity(current_user, request)


@router.get("/{user_id}/profile", response_model=FullUserProfileResponse)
async def get_user_profile_by_id(
    user_id: int,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    Screen 12: public runner profile (email only shown to its owner). A runner
    hidden by their privacy level comes back as a minimal `is_restricted` card.
    """
    return await _build_full_profile(user_id, current_user["id"] if current_user else None)


@router.get("/{user_id}/runs", response_model=RunHistoryResponse)
async def get_user_runs(
    user_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    Screen 12: paginated run history (own runs only).
    """
    if current_user["id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to access these runs")

    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, activity_id, title, start_time, status, distance_km, duration_seconds,
                   calories, is_claimed, claim_outcome
            FROM runs WHERE user_id = $1
            ORDER BY start_time DESC, id DESC
            LIMIT $2 OFFSET $3
            """,
            user_id, limit, offset,
        )
        total = await conn.fetchval("SELECT count(*) FROM runs WHERE user_id = $1", user_id)

    runs = []
    for r in rows:
        claimed = r["claim_outcome"] in ("success", "takeover", "fortified")
        runs.append({
            "id": r["id"],
            "activity_id": r["activity_id"],
            "title": r["title"],
            "date": iso(r["start_time"]),
            "distance_km": round(r["distance_km"], 2),
            "duration": format_duration(r["duration_seconds"]),
            "duration_seconds": r["duration_seconds"],
            "pace": format_pace(pace_seconds_per_km(r["duration_seconds"], r["distance_km"])),
            "pace_seconds": pace_seconds_per_km(r["duration_seconds"], r["distance_km"]),
            "calories": r["calories"],
            "status": r["status"],
            "is_claimed": r["is_claimed"],
            "badge": "Claimed Base" if claimed else "Open Run",
        })
    return {"user_id": str(user_id), "total_runs": total, "limit": limit, "offset": offset, "runs": runs}


@router.get("/{user_id}/territories", response_model=BaseListResponse)
async def get_user_territories(user_id: int, current_user: Optional[dict] = Depends(get_current_user_optional)):
    """
    Screen 12: territories currently held by a runner (403 when their privacy level hides them).
    """
    async with connection() as conn:
        await privacy.require_visible(conn, user_id, current_user["id"] if current_user else None)
        rows = await conn.fetch(
            TERRITORY_SELECT + " WHERE t.owner_id = $1 AND t.status = 'active' ORDER BY t.id DESC", user_id
        )
    return {"total": len(rows), "bases": [base_item(r) for r in rows]}


@router.get("/{user_id}/achievements", response_model=AchievementsResponse)
async def get_user_achievements(user_id: int, current_user: dict = Depends(get_current_user)):
    """
    Screen 12: unlocked badges of a runner (403 when their privacy level hides them).
    """
    async with connection() as conn:
        exists = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users WHERE id = $1)", user_id)
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")
        await privacy.require_visible(conn, user_id, current_user["id"])
        rows = await conn.fetch(
            """
            SELECT a.id, a.key, a.title, a.description, a.badge_icon, ua.unlocked_at
            FROM user_achievements ua JOIN achievements a ON a.id = ua.achievement_id
            WHERE ua.user_id = $1
            ORDER BY ua.unlocked_at, a.id
            """,
            user_id,
        )
    return {
        "user_id": str(user_id),
        "total_badges": len(rows),
        "achievements": [{**dict(r), "unlocked_at": iso(r["unlocked_at"])} for r in rows],
    }
