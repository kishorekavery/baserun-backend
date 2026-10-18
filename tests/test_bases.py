import uuid
from datetime import datetime, timezone


def make_valid_loop(user_id="user_test"):
    return {
        "user_id": user_id,
        "points": [
            {"latitude": 13.0827, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0837, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0837, "longitude": 80.2717, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0827, "longitude": 80.2717, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0827, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    }


def test_claim_base_success(client):
    payload = make_valid_loop("runner_claim_1")
    res = client.post("/api/v1/bases/claim", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["base_id"] is not None
    assert data["area"] > 100
    assert data["perimeter"] > 0
    assert "POLYGON" in data["polygon_wkt"]


def test_claim_base_not_enough_points(client):
    payload = {
        "user_id": "short_runner",
        "points": [
            {"latitude": 13.0827, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0837, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0837, "longitude": 80.2717, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    }
    res = client.post("/api/v1/bases/claim", json=payload)
    assert res.status_code == 400
    assert "not enough points" in res.json()["detail"].lower()


def test_claim_base_too_small_anticheat(client):
    # Very tiny area (less than 1 meter displacement)
    payload = {
        "user_id": "tiny_runner",
        "points": [
            {"latitude": 13.082700, "longitude": 80.270700, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.082701, "longitude": 80.270700, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.082701, "longitude": 80.270701, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.082700, "longitude": 80.270701, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.082700, "longitude": 80.270700, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    }
    res = client.post("/api/v1/bases/claim", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "failed"
    assert "too small" in data["message"].lower()


def test_claim_base_too_large_anticheat(client):
    # Giant area (e.g. crossing entire states, > 500,000 sq meters)
    payload = {
        "user_id": "giant_runner",
        "points": [
            {"latitude": 10.0, "longitude": 75.0, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 15.0, "longitude": 75.0, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 15.0, "longitude": 80.0, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 10.0, "longitude": 80.0, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 10.0, "longitude": 75.0, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    }
    res = client.post("/api/v1/bases/claim", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "failed"
    assert "too large" in data["message"].lower()


def test_get_bases_list(client):
    # Claim at least one base
    client.post("/api/v1/bases/claim", json=make_valid_loop("runner_list_test"))

    res = client.get("/api/v1/bases?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert "total" in data
    assert "bases" in data
    assert data["total"] >= 1
    assert len(data["bases"]) >= 1


def test_get_bases_filtered_by_user(client):
    target_user = f"user_{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/bases/claim", json=make_valid_loop(target_user))

    res = client.get(f"/api/v1/bases?user_id={target_user}")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["bases"][0]["user_id"] == target_user


def test_get_single_base_by_id(client):
    res_claim = client.post("/api/v1/bases/claim", json=make_valid_loop("single_base_runner"))
    base_id = res_claim.json()["base_id"]

    res = client.get(f"/api/v1/bases/{base_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == base_id
    assert data["user_id"] == "single_base_runner"
    assert data["status"] == "active"
    assert "POLYGON" in data["polygon_wkt"]


def test_get_single_base_not_found(client):
    res = client.get("/api/v1/bases/999999")
    assert res.status_code == 404


def test_get_user_stats(client):
    stats_user = f"stats_user_{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/bases/claim", json=make_valid_loop(stats_user))
    client.post("/api/v1/bases/claim", json=make_valid_loop(stats_user))

    res = client.get(f"/api/v1/bases/user/{stats_user}/stats")
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == stats_user
    assert data["total_bases"] == 2
    assert data["total_area_sq_meters"] > 0
    assert data["total_perimeter_meters"] > 0
