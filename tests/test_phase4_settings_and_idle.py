import os
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from db.db_conn import sqlite_connection


def test_phase4_settings_exist_and_legacy_gone():
    """
    Verify SESSION_TTL_SECONDS, SESSION_MODE, MAX_SESSIONS_PER_USER, SESSION_ABSOLUTE_MAX_SECONDS
    exist and ACCESS_TOKEN_MINUTES is removed from core.
    """
    from routers import core
    assert hasattr(core, "SESSION_TTL_SECONDS")
    assert core.SESSION_TTL_SECONDS == 86400

    assert hasattr(core, "SESSION_MODE")
    assert core.SESSION_MODE in ["idle", "absolute"]

    assert hasattr(core, "MAX_SESSIONS_PER_USER")
    assert core.MAX_SESSIONS_PER_USER >= 1

    assert hasattr(core, "SESSION_ABSOLUTE_MAX_SECONDS")
    assert core.SESSION_ABSOLUTE_MAX_SECONDS == 2592000

    assert not hasattr(core, "ACCESS_TOKEN_MINUTES"), "ACCESS_TOKEN_MINUTES should be completely removed"


@pytest.mark.asyncio
async def test_phase4_sessions_table_has_last_seen_at():
    """
    Verify the sessions table in database contains last_seen_at column.
    """
    conn = await sqlite_connection()
    try:
        async with conn.execute("PRAGMA table_info(sessions)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
            assert "last_seen_at" in columns, f"last_seen_at column missing from sessions. Found columns: {columns}"
    finally:
        await conn.close()


def test_phase4_idle_mode_extends_expires_at(client: TestClient, monkeypatch):
    """
    In idle mode, a request made after the 5-minute throttle must extend expires_at.
    """
    monkeypatch.setenv("SESSION_MODE", "idle")
    from routers import core
    monkeypatch.setattr(core, "SESSION_MODE", "idle")

    # Register user
    user_payload = {
        "username": f"idle_user_{uuid.uuid4().hex[:6]}",
        "email": f"idle_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res = client.post("/api/v1/auth/register", json=user_payload)
    assert res.status_code == 200
    token = res.json()["access_token"]
    session_id = res.json()["session_id"]
    headers = {"Authorization": f"Bearer {token}"}

    import asyncio
    async def get_session_times():
        conn = await sqlite_connection()
        try:
            async with conn.execute("SELECT expires_at, last_seen_at FROM sessions WHERE session_id = ?", (session_id,)) as cur:
                return await cur.fetchone()
        finally:
            await conn.close()

    initial_row = asyncio.run(get_session_times())
    initial_expires_at = initial_row[0]

    # Simulate 6 minutes passing by setting last_seen_at to 6 minutes ago in DB
    async def set_past_last_seen():
        conn = await sqlite_connection()
        try:
            six_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
            await conn.execute("UPDATE sessions SET last_seen_at = ? WHERE session_id = ?", (six_mins_ago, session_id))
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(set_past_last_seen())

    # Make a protected request
    res_req = client.get("/api/v1/bases/me", headers=headers)
    assert res_req.status_code == 200

    # Verify expires_at moved forward
    updated_row = asyncio.run(get_session_times())
    updated_expires_at = updated_row[0]
    assert updated_expires_at > initial_expires_at, "expires_at should have moved forward in idle mode after 5 minutes"


def test_phase4_absolute_mode_leaves_expires_at_fixed(client: TestClient, monkeypatch):
    """
    In absolute mode, a request made after 5 minutes must NOT extend expires_at.
    """
    monkeypatch.setenv("SESSION_MODE", "absolute")
    from routers import core
    monkeypatch.setattr(core, "SESSION_MODE", "absolute")

    user_payload = {
        "username": f"abs_user_{uuid.uuid4().hex[:6]}",
        "email": f"abs_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res = client.post("/api/v1/auth/register", json=user_payload)
    assert res.status_code == 200
    token = res.json()["access_token"]
    session_id = res.json()["session_id"]
    headers = {"Authorization": f"Bearer {token}"}

    import asyncio
    async def get_session_times():
        conn = await sqlite_connection()
        try:
            async with conn.execute("SELECT expires_at FROM sessions WHERE session_id = ?", (session_id,)) as cur:
                return await cur.fetchone()
        finally:
            await conn.close()

    initial_row = asyncio.run(get_session_times())
    initial_expires_at = initial_row[0]

    # Set last_seen_at to 6 minutes ago
    async def set_past_last_seen():
        conn = await sqlite_connection()
        try:
            six_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
            await conn.execute("UPDATE sessions SET last_seen_at = ? WHERE session_id = ?", (six_mins_ago, session_id))
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(set_past_last_seen())

    res_req = client.get("/api/v1/bases/me", headers=headers)
    assert res_req.status_code == 200

    updated_row = asyncio.run(get_session_times())
    updated_expires_at = updated_row[0]
    assert updated_expires_at == initial_expires_at, "expires_at must stay fixed in absolute mode"


def test_phase4_max_sessions_per_user_revokes_oldest(client: TestClient, monkeypatch):
    """
    With MAX_SESSIONS_PER_USER=2, logging in a 3rd time revokes the oldest session (session 1),
    leaving sessions 2 and 3 active.
    """
    monkeypatch.setenv("MAX_SESSIONS_PER_USER", "2")
    from routers import core
    monkeypatch.setattr(core, "MAX_SESSIONS_PER_USER", 2)

    username = f"multi_{uuid.uuid4().hex[:6]}"
    password = "Password123!"
    reg_res = client.post("/api/v1/auth/register", json={
        "username": username,
        "email": f"{username}@example.com",
        "password": password,
    })
    token1 = reg_res.json()["access_token"]

    # Login 2nd time -> token 2
    login2 = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    token2 = login2.json()["access_token"]

    # Both token 1 and token 2 should work
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token1}"}).status_code == 200
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token2}"}).status_code == 200

    # Login 3rd time -> token 3
    login3 = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    token3 = login3.json()["access_token"]

    # Now token 1 should be revoked (401), while token 2 and 3 remain active (200)
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token1}"}).status_code == 401
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token2}"}).status_code == 200
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token3}"}).status_code == 200


def test_phase4_social_login_revokes_old_sessions(client: TestClient, monkeypatch):
    """
    Social login must revoke older sessions beyond MAX_SESSIONS_PER_USER (default 1).
    """
    monkeypatch.setenv("MAX_SESSIONS_PER_USER", "1")
    from routers import core
    monkeypatch.setattr(core, "MAX_SESSIONS_PER_USER", 1)

    email = f"social_{uuid.uuid4().hex[:6]}@example.com"
    social_payload = {
        "provider": "google",
        "token": "fake-google-id-token",
        "email": email,
        "name": "Social Runner",
    }

    # Social login 1
    res1 = client.post("/api/v1/auth/social-login", json=social_payload)
    assert res1.status_code == 200
    token1 = res1.json()["access_token"]
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token1}"}).status_code == 200

    # Social login 2
    res2 = client.post("/api/v1/auth/social-login", json=social_payload)
    assert res2.status_code == 200
    token2 = res2.json()["access_token"]

    # token 1 must now be revoked (401) and token 2 active (200)
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token1}"}).status_code == 401
    assert client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token2}"}).status_code == 200
