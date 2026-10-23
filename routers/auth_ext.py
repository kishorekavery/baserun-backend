import hashlib
import logging
import re
import secrets

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from db.database import connection
from models.datamodel import (
    CheckUsernameResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    Message,
    RegisterRequest,
    RegisterResponse,
    SocialLoginRequest,
)
from routers import core
from routers.core import build_auth_response, get_current_user, hash_password
from utils.helper import is_valid_username
from utils.social_auth import SocialAuthError, allow_unverified, verify_social_token

logger = logging.getLogger("baserun.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

SUPPORTED_PROVIDERS = {"google", "apple"}


@router.post("/login", response_model=LoginResponse)
async def auth_login(request: LoginRequest):
    """
    Flutter Login Endpoint: POST /auth/login (username or email in `username`).
    """
    return await core.login(request)


@router.post("/register", response_model=RegisterResponse)
async def auth_register(request: RegisterRequest):
    """
    Flutter Register Endpoint: POST /auth/register
    """
    return await core.register_user(request)


@router.post("/logout", response_model=LogoutResponse)
async def auth_logout(current_user: dict = Depends(get_current_user)):
    """
    Flutter Logout Endpoint: POST /auth/logout
    Invalidates current session on the server.
    """
    return await core.logout(current_user)


@router.get("/check-username", response_model=CheckUsernameResponse)
async def check_username(username: str = Query(..., min_length=1, max_length=64)):
    """
    Screen 3: real-time, case-insensitive username availability check.
    """
    username = username.strip()
    if not is_valid_username(username):
        return {
            "username": username,
            "available": False,
            "message": "Username must be 3-32 characters of letters, numbers, '_', '.' or '-'",
        }
    async with connection() as conn:
        taken = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM users WHERE LOWER(username) = LOWER($1))", username
        )
    if taken:
        return {"username": username, "available": False, "message": "Username is already taken"}
    return {"username": username, "available": True, "message": "Username is available"}


def _username_base(name: str | None, provider: str) -> str:
    base = re.sub(r"[^a-z0-9_.-]", "", (name or "").lower().replace(" ", "_"))[:24]
    return base if len(base) >= 3 else f"runner_{provider}"


@router.post("/social-login", response_model=LoginResponse)
async def social_login(request: SocialLoginRequest):
    """
    Screen 2: Continue with Google / Apple. The provider ID token is verified
    and the account is found (or created) by the verified email.
    """
    if request.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported social provider")

    if allow_unverified():
        token_hash = hashlib.sha256(request.token.encode()).hexdigest()[:16]
        email = request.email or f"{request.provider}_{token_hash}@social.baserun.app"
        name = request.name
    else:
        try:
            identity = await verify_social_token(request.provider, request.token)
        except SocialAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        email = identity.email or f"{request.provider}_{identity.subject}@social.baserun.app"
        name = identity.name or request.name
    email = email.lower()

    async with connection() as conn:
        user_id = await conn.fetchval("SELECT id FROM users WHERE LOWER(email) = $1", email)
        is_new = user_id is None
        if is_new:
            base = _username_base(name, request.provider)
            random_password = await hash_password(secrets.token_hex(32))
            for attempt in range(6):
                username = base if attempt == 0 else f"{base[:24]}_{secrets.token_hex(3)}"
                try:
                    user_id = await conn.fetchval(
                        """
                        INSERT INTO users (username, email, hashed_password, first_name, last_name, auth_provider)
                        VALUES ($1, $2, $3, $4, '', $5)
                        RETURNING id
                        """,
                        username, email, random_password, (name or "Runner")[:64], request.provider,
                    )
                    break
                except asyncpg.UniqueViolationError as exc:
                    if "email" in (exc.constraint_name or ""):
                        # Created concurrently by another login request
                        user_id = await conn.fetchval("SELECT id FROM users WHERE LOWER(email) = $1", email)
                        is_new = False
                        break
            if user_id is None:
                raise HTTPException(status_code=409, detail="Could not allocate a unique username, please retry")
        return await build_auth_response(conn, user_id, Message.LOGIN_SUCCESSFUL, is_new_user=is_new)
