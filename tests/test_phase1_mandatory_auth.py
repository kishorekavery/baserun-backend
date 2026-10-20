import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient


def test_phase1_unauthenticated_endpoints_return_401(client: TestClient):
    """
    Every core endpoint must strictly return 401 Unauthorized when called without a token.
    """
    dummy_uuid = str(uuid.uuid4())
    sample_points = [
        {"latitude": 13.0827, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()}
    ]

    # 1. /runs/start
    res = client.post("/api/v1/runs/start", json={"title": "Morning Run"})
    assert res.status_code == 401, f"/runs/start returned {res.status_code}, expected 401"

    # 2. /runs/{id}/telemetry
    res = client.post(f"/api/v1/runs/{dummy_uuid}/telemetry", json={
        "activity_id": dummy_uuid,
        "points": sample_points
    })
    assert res.status_code == 401, f"/runs/{{id}}/telemetry returned {res.status_code}, expected 401"

    # 3. /runs/{id}/pause
    res = client.post(f"/api/v1/runs/{dummy_uuid}/pause")
    assert res.status_code == 401, f"/runs/{{id}}/pause returned {res.status_code}, expected 401"

    # 4. /runs/{id}/resume
    res = client.post(f"/api/v1/runs/{dummy_uuid}/resume")
    assert res.status_code == 401, f"/runs/{{id}}/resume returned {res.status_code}, expected 401"

    # 5. /runs/{id}/finish
    res = client.post(f"/api/v1/runs/{dummy_uuid}/finish", json={"steps": 1000})
    assert res.status_code == 401, f"/runs/{{id}}/finish returned {res.status_code}, expected 401"

    # 6. /bases/claim
    claim_coords = [
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0830, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0830, "longitude": 80.2710, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0820, "longitude": 80.2710, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
    ]
    res = client.post("/api/v1/bases/claim", json={"points": claim_coords})
    assert res.status_code == 401, f"/bases/claim returned {res.status_code}, expected 401"

    # 7. /bases/telemetry
    res = client.post("/api/v1/bases/telemetry", json={
        "activity_id": dummy_uuid,
        "points": sample_points
    })
    assert res.status_code == 401, f"/bases/telemetry returned {res.status_code}, expected 401"

    # 8. /territories/claim-from-run
    res = client.post("/api/v1/territories/claim-from-run", json={"activity_id": dummy_uuid})
    assert res.status_code == 401, f"/territories/claim-from-run returned {res.status_code}, expected 401"

    # 9. /territories/{id}/challenge
    res = client.post("/api/v1/territories/1/challenge", json={})
    assert res.status_code == 401, f"/territories/{{id}}/challenge returned {res.status_code}, expected 401"

    # 10. /clubs/{id}/join
    res = client.post("/api/v1/clubs/1/join")
    assert res.status_code == 401, f"/clubs/{{id}}/join returned {res.status_code}, expected 401"

    # 11. GET /users/{user_id}/runs
    res = client.get("/api/v1/users/1/runs")
    assert res.status_code == 401, f"GET /users/1/runs returned {res.status_code}, expected 401"
    res_root = client.get("/users/1/runs")
    assert res_root.status_code == 401, f"GET /users/1/runs (root) returned {res_root.status_code}, expected 401"


def test_phase1_user_runs_privacy_owner_vs_other_user(client: TestClient):
    """
    GET /users/{user_id}/runs:
    - 401 when unauthenticated.
    - 403 Forbidden when User B tries to view User A's runs.
    - 200 OK when User A views their own runs.
    """
    # Register and login User A
    user_a = {
        "username": f"user_a_{uuid.uuid4().hex[:6]}",
        "email": f"a_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res_a = client.post("/api/v1/auth/register", json=user_a)
    assert res_a.status_code == 200
    token_a = res_a.json()["access_token"]
    user_a_id = res_a.json()["user_id"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Register and login User B
    user_b = {
        "username": f"user_b_{uuid.uuid4().hex[:6]}",
        "email": f"b_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res_b = client.post("/api/v1/auth/register", json=user_b)
    assert res_b.status_code == 200
    token_b = res_b.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 1. Unauthenticated -> 401
    res = client.get(f"/api/v1/users/{user_a_id}/runs")
    assert res.status_code == 401

    # 2. User B viewing User A -> 403 Forbidden
    res = client.get(f"/api/v1/users/{user_a_id}/runs", headers=headers_b)
    assert res.status_code == 403
    assert "Forbidden" in res.json().get("detail", "")

    # 3. User A viewing their own runs -> 200 OK
    res = client.get(f"/api/v1/users/{user_a_id}/runs", headers=headers_a)
    assert res.status_code == 200
    assert res.json()["user_id"] == str(user_a_id)


def test_phase1_identity_taken_only_from_token(client: TestClient):
    """
    Verify client-sent user_id or challenger_id is ignored and the identity
    is taken exclusively from the authenticated JWT token.
    """
    user_payload = {
        "username": f"runner_{uuid.uuid4().hex[:6]}",
        "email": f"runner_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res = client.post("/api/v1/auth/register", json=user_payload)
    assert res.status_code == 200
    token = res.json()["access_token"]
    real_user_id = str(res.json()["user_id"])
    headers = {"Authorization": f"Bearer {token}"}

    # Start run with a spoofed user_id in the body
    res_start = client.post(
        "/api/v1/runs/start",
        json={"title": "Test Run", "user_id": "999999"},
        headers=headers,
    )
    assert res_start.status_code == 200
    data = res_start.json()
    assert str(data["user_id"]) == real_user_id
    assert str(data["user_id"]) != "999999"
