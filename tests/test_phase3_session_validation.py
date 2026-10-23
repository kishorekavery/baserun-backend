import os
import uuid
from datetime import datetime, timedelta, timezone
import jwt
import pytest
from fastapi.testclient import TestClient

from tests.conftest import db_run

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "test-secret-key-for-pytest-runs-32bytes-minimum")
ALGORITHM = "HS256"


@pytest.fixture
def test_user(client: TestClient):
    user_payload = {
        "username": f"sess_user_{uuid.uuid4().hex[:6]}",
        "email": f"sess_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res = client.post("/api/v1/auth/register", json=user_payload)
    assert res.status_code == 200
    data = res.json()
    return {
        "user_id": data["user_id"],
        "token": data["access_token"],
        "session_id": data["session_id"],
    }


def test_phase3_legacy_token_missing_jti_rejected(client: TestClient, test_user):
    """
    Tokens missing a 'jti' claim (legacy tokens) must be rejected with 401.
    """
    user_id = test_user["user_id"]
    # Craft a JWT without 'jti' claim
    legacy_payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    legacy_token = jwt.encode(legacy_payload, SECRET_KEY, algorithm=ALGORITHM)

    res = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {legacy_token}"})
    assert res.status_code == 401, f"Expected 401 for token missing jti, got {res.status_code}"
    assert "jti" in res.json().get("detail", "").lower() or "not authenticated" in res.json().get("detail", "").lower() or "credentials" in res.json().get("detail", "").lower() or "invalid" in res.json().get("detail", "").lower()


def test_phase3_unknown_jti_rejected(client: TestClient, test_user):
    """
    Tokens with a jti that does not exist in the database sessions table must return 401.
    """
    user_id = test_user["user_id"]
    fake_jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "jti": fake_jti,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    res = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401, f"Expected 401 for unknown jti, got {res.status_code}"


def test_phase3_expired_db_session_rejected(client: TestClient, test_user):
    """
    Even if the JWT cryptographic expiration hasn't arrived, an expired DB session must return 401.
    """
    token = test_user["token"]
    session_id = test_user["session_id"]

    past_time = datetime.now(timezone.utc) - timedelta(hours=2)
    db_run("UPDATE sessions SET expires_at = $1 WHERE session_id = $2", past_time, session_id)

    res = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401, f"Expected 401 for expired DB session, got {res.status_code}"
    assert "expired" in res.json().get("detail", "").lower()


def test_phase3_revoked_session_rejected(client: TestClient, test_user):
    """
    A revoked session must return 401.
    """
    token = test_user["token"]
    session_id = test_user["session_id"]

    db_run("UPDATE sessions SET is_revoked = TRUE WHERE session_id = $1", session_id)

    res = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert "revoked" in res.json().get("detail", "").lower()


def test_phase3_valid_session_succeeds(client: TestClient, test_user):
    """
    A valid session returns 200.
    """
    token = test_user["token"]
    res = client.get("/api/v1/bases/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert str(res.json()["id"]) == str(test_user["user_id"])
