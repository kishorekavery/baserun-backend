import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def users_a_and_b(client: TestClient):
    # Register User A
    user_a = {
        "username": f"runner_a_{uuid.uuid4().hex[:6]}",
        "email": f"a_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res_a = client.post("/api/v1/auth/register", json=user_a)
    assert res_a.status_code == 200
    token_a = res_a.json()["access_token"]
    user_a_id = res_a.json()["user_id"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Register User B
    user_b = {
        "username": f"runner_b_{uuid.uuid4().hex[:6]}",
        "email": f"b_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    }
    res_b = client.post("/api/v1/auth/register", json=user_b)
    assert res_b.status_code == 200
    token_b = res_b.json()["access_token"]
    user_b_id = res_b.json()["user_id"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    return {
        "a": {"id": user_a_id, "headers": headers_a},
        "b": {"id": user_b_id, "headers": headers_b},
    }


def test_user_b_cannot_pause_user_a_run(client: TestClient, users_a_and_b):
    headers_a = users_a_and_b["a"]["headers"]
    headers_b = users_a_and_b["b"]["headers"]

    # User A starts run
    res_start = client.post("/api/v1/runs/start", json={"title": "A's Run"}, headers=headers_a)
    assert res_start.status_code == 200
    act_id = res_start.json()["activity_id"]

    # User B tries to pause User A's run -> expect 403 Forbidden
    res_b_pause = client.post(f"/api/v1/runs/{act_id}/pause", headers=headers_b)
    assert res_b_pause.status_code == 403
    assert res_b_pause.json()["detail"] == "Forbidden: You do not have permission to modify this resource"

    # User A pauses their own run -> expect 200 OK
    res_a_pause = client.post(f"/api/v1/runs/{act_id}/pause", headers=headers_a)
    assert res_a_pause.status_code == 200
    assert res_a_pause.json()["status"] == "paused"


def test_user_b_cannot_resume_user_a_run(client: TestClient, users_a_and_b):
    headers_a = users_a_and_b["a"]["headers"]
    headers_b = users_a_and_b["b"]["headers"]

    # User A starts and pauses run
    res_start = client.post("/api/v1/runs/start", json={"title": "A's Run"}, headers=headers_a)
    act_id = res_start.json()["activity_id"]
    client.post(f"/api/v1/runs/{act_id}/pause", headers=headers_a)

    # User B tries to resume User A's run -> expect 403 Forbidden
    res_b_resume = client.post(f"/api/v1/runs/{act_id}/resume", headers=headers_b)
    assert res_b_resume.status_code == 403
    assert res_b_resume.json()["detail"] == "Forbidden: You do not have permission to modify this resource"

    # User A resumes their own run -> expect 200 OK
    res_a_resume = client.post(f"/api/v1/runs/{act_id}/resume", headers=headers_a)
    assert res_a_resume.status_code == 200
    assert res_a_resume.json()["status"] == "recording"


def test_user_b_cannot_finish_user_a_run(client: TestClient, users_a_and_b):
    headers_a = users_a_and_b["a"]["headers"]
    headers_b = users_a_and_b["b"]["headers"]

    # User A starts run
    res_start = client.post("/api/v1/runs/start", json={"title": "A's Run"}, headers=headers_a)
    act_id = res_start.json()["activity_id"]

    # User B tries to finish User A's run -> expect 403 Forbidden
    res_b_finish = client.post(
        f"/api/v1/runs/{act_id}/finish",
        json={"steps": 5000, "calories": 300},
        headers=headers_b,
    )
    assert res_b_finish.status_code == 403
    assert res_b_finish.json()["detail"] == "Forbidden: You do not have permission to modify this resource"

    # User A finishes their own run -> expect 200 OK
    res_a_finish = client.post(
        f"/api/v1/runs/{act_id}/finish",
        json={"steps": 5000, "calories": 300},
        headers=headers_a,
    )
    assert res_a_finish.status_code == 200
    assert res_a_finish.json()["status"] == "completed"


def test_user_b_cannot_stream_telemetry_to_user_a_run(client: TestClient, users_a_and_b):
    headers_a = users_a_and_b["a"]["headers"]
    headers_b = users_a_and_b["b"]["headers"]

    # User A starts run
    res_start = client.post("/api/v1/runs/start", json={"title": "A's Run"}, headers=headers_a)
    act_id = res_start.json()["activity_id"]

    sample_pts = {
        "activity_id": act_id,
        "points": [
            {
                "latitude": 13.0827,
                "longitude": 80.2707,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 5.0,
                "speed": 3.0,
            }
        ],
    }

    # User B tries to send telemetry to User A's run -> expect 403 Forbidden
    res_b_telem = client.post(
        f"/api/v1/runs/{act_id}/telemetry",
        json=sample_pts,
        headers=headers_b,
    )
    assert res_b_telem.status_code == 403
    assert res_b_telem.json()["detail"] == "Forbidden: You do not have permission to modify this resource"

    # User A streams telemetry to their own run -> expect 200 OK
    res_a_telem = client.post(
        f"/api/v1/runs/{act_id}/telemetry",
        json=sample_pts,
        headers=headers_a,
    )
    assert res_a_telem.status_code == 200
    assert res_a_telem.json()["saved_points"] == 1


def test_user_b_cannot_claim_territory_from_user_a_run(client: TestClient, users_a_and_b):
    headers_a = users_a_and_b["a"]["headers"]
    headers_b = users_a_and_b["b"]["headers"]

    # User A starts run
    res_start = client.post("/api/v1/runs/start", json={"title": "A's Run"}, headers=headers_a)
    act_id = res_start.json()["activity_id"]

    # User B tries to claim territory from User A's run -> expect 403 Forbidden
    res_b_claim = client.post(
        "/api/v1/territories/claim-from-run",
        json={"activity_id": act_id, "territory_name": "Stolen Turf"},
        headers=headers_b,
    )
    assert res_b_claim.status_code == 403
    assert res_b_claim.json()["detail"] == "Forbidden: You do not have permission to modify this resource"


def test_ownership_checks_on_aliased_routes(client: TestClient, users_a_and_b):
    """Verify ownership checks are enforced on root-level alias /runs/... as well."""
    headers_a = users_a_and_b["a"]["headers"]
    headers_b = users_a_and_b["b"]["headers"]

    # User A starts run via unversioned alias /runs/start
    res_start = client.post("/runs/start", json={"title": "A's Alias Run"}, headers=headers_a)
    assert res_start.status_code == 200
    act_id = res_start.json()["activity_id"]

    # User B tries to pause via /runs/{id}/pause -> 403
    res_b_pause = client.post(f"/runs/{act_id}/pause", headers=headers_b)
    assert res_b_pause.status_code == 403
    assert res_b_pause.json()["detail"] == "Forbidden: You do not have permission to modify this resource"

    # User A pauses via /runs/{id}/pause -> 200
    res_a_pause = client.post(f"/runs/{act_id}/pause", headers=headers_a)
    assert res_a_pause.status_code == 200
    assert res_a_pause.json()["status"] == "paused"
