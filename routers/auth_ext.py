from datetime import datetime, timedelta, timezone
import secrets
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from db.db_conn import sqlite_connection, fetch_record, insert_records, insert_record_get_id
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
from routers.core import create_access_token, password_hash, get_current_user, create_user_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def auth_login(request: LoginRequest):
    """
    Flutter Login Endpoint: POST /auth/login
    """
    from routers import core
    return await core.login(request)


@router.post("/register", response_model=RegisterResponse)
async def auth_register(request: RegisterRequest):
    """
    Flutter Register Endpoint: POST /auth/register
    """
    from routers import core
    return await core.register_user(request)


@router.post("/logout", response_model=LogoutResponse)
async def auth_logout(current_user: dict = Depends(get_current_user)):
    """
    Flutter Logout Endpoint: POST /auth/logout
    Invalidates current session on the server.
    """
    from routers import core
    return await core.logout(current_user)


@router.get("/check-username", response_model=CheckUsernameResponse)
async def check_username(username: str = Query(..., min_length=3, max_length=32)):
    """
    Screen 3: Register - Real-time username availability check.
    Shows the green checkmark on Screen 3 & 4.
    """
    conn = await sqlite_connection()
    try:
        user = await fetch_record(conn, "users", "username", (username,))
        if user:
            return {
                "username": username,
                "available": False,
                "message": "Username is already taken"
            }
        return {
            "username": username,
            "available": True,
            "message": "Username is available"
        }
    finally:
        await conn.close()


@router.post("/social-login", response_model=LoginResponse)
async def social_login(request: SocialLoginRequest):
    """
    Screen 2: Login - Continue with Google or Continue with Apple.
    Authenticates existing social user or creates a seamless runner account.
    """
    if request.provider.lower() not in ["google", "apple"]:
        raise HTTPException(status_code=400, detail="Unsupported social provider")

    email = request.email or f"{request.provider.lower()}_{uuid.uuid4().hex[:8]}@social.baserun.com"
    username = (request.name or f"runner_{request.provider.lower()}_{uuid.uuid4().hex[:6]}").replace(" ", "").lower()

    conn = await sqlite_connection()
    try:
        user = await fetch_record(conn, "users", "email", (email,))
        if not user:
            # Create user on first social login
            random_pw = password_hash.hash(uuid.uuid4().hex)
            user_id = await insert_record_get_id(
                conn,
                "users",
                ["username", "hashed_password", "first_name", "last_name", "email", "phone"],
                [username, random_pw, request.name or "Runner", "", email, ""],
            )
        else:
            user_id = user["id"]

        session_id, token, expires_in, expire_iso = await create_user_session(conn, user_id)

        return {
            "access_token": token,
            "token": token,
            "token_type": "bearer",
            "message": Message.LOGIN_SUCCESSFUL,
            "expires_in": expires_in,
            "expires_at": expire_iso,
            "session_id": session_id,
        }
    finally:
        await conn.close()

