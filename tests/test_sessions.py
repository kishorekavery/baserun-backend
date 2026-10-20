import uuid
import pytest
from db.db_conn import sqlite_connection, execute_query_one, cleanup_expired_sessions
from routers.core import ACCESS_TOKEN_MINUTES


def test_default_token_expiry_is_24_hours():
    # 1440 minutes = 24 hours = 1 day
    assert ACCESS_TOKEN_MINUTES == 1440


def test_register_creates_server_side_session(client):
    username = f"sess_reg_{uuid.uuid4().hex[:6]}"
    res = client.post("/api/v1/bases/register", json={
        "username": username,
        "password": "Password123!",
        "email": f"{username}@example.com",
        "first_name": "Session",
    })
    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    assert data["session_id"] is not None
    assert len(data["session_id"]) >= 32
    assert "expires_at" in data
    assert data["expires_in"] == 1440 * 60


def test_login_creates_and_rotates_session(client, registered_user):
    # 1. First login
    res1 = client.post("/api/v1/bases/login", json={
        "username": registered_user["username"],
        "password": registered_user["password"]
    })
    assert res1.status_code == 200
    data1 = res1.json()
    token1 = data1["access_token"]
    session1 = data1["session_id"]
    assert session1 is not None

    # First token works
    res_me1 = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token1}"})
    assert res_me1.status_code == 200

    # 2. Second login (creates new session, invalidates old session)
    res2 = client.post("/api/v1/bases/login", json={
        "username": registered_user["username"],
        "password": registered_user["password"]
    })
    assert res2.status_code == 200
    data2 = res2.json()
    token2 = data2["access_token"]
    session2 = data2["session_id"]
    assert session2 != session1

    # Second token works
    res_me2 = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token2}"})
    assert res_me2.status_code == 200

    # First token is now revoked on the server
    res_me1_revoked = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token1}"})
    assert res_me1_revoked.status_code == 401
    assert "revoked" in res_me1_revoked.json()["detail"].lower()


def test_logout_endpoint_and_revocation(client):
    # 1. Register new runner
    username = f"logout_user_{uuid.uuid4().hex[:6]}"
    res = client.post("/api/v1/bases/register", json={
        "username": username,
        "password": "Password123!",
        "email": f"{username}@example.com",
    })
    assert res.status_code == 200
    token = res.json()["access_token"]

    # 2. Verify token works
    headers = {"Authorization": f"Bearer {token}"}
    res_me = client.get("/api/v1/bases/me", headers=headers)
    assert res_me.status_code == 200

    # 3. Call logout endpoint
    res_logout = client.post("/api/v1/bases/logout", headers=headers)
    assert res_logout.status_code == 200
    assert res_logout.json()["status"] == "success"

    # 4. Attempting to use the logged-out token now returns 401
    res_after_logout = client.get("/api/v1/bases/me", headers=headers)
    assert res_after_logout.status_code == 401
    assert "revoked" in res_after_logout.json()["detail"].lower()


def test_auth_ext_logout_endpoint(client):
    username = f"ext_logout_{uuid.uuid4().hex[:6]}"
    res = client.post("/auth/register", json={
        "username": username,
        "password": "Password123!",
        "email": f"{username}@example.com",
    })
    assert res.status_code == 200
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Logout via /auth/logout
    res_logout = client.post("/auth/logout", headers=headers)
    assert res_logout.status_code == 200
    assert res_logout.json()["status"] == "success"

    # Subsequent access rejected
    res_denied = client.get("/users/me", headers=headers)
    assert res_denied.status_code == 401


@pytest.mark.asyncio
async def test_cleanup_expired_sessions_helper():
    cleaned = await cleanup_expired_sessions()
    assert isinstance(cleaned, int)
