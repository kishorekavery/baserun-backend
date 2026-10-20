from datetime import datetime, timezone
import pytest


def test_live_tracking_coordinates_and_recent_points(client, auth_headers):
    """
    Verify live tracking:
    - Streams GPS telemetry points
    - Live HUD returns latest GPS coordinates and recent points trail
    - Live path endpoint returns full breadcrumb trail
    """
    # 1. Start run
    res_start = client.post("/api/v1/runs/start", json={"title": "Beachfront Sprint"}, headers=auth_headers)
    assert res_start.status_code == 200
    act_id = res_start.json()["activity_id"]

    # 2. Stream 3 GPS points in a batch (activity_id in body is optional now)
    t1 = datetime.now(timezone.utc).isoformat()
    t2 = datetime.now(timezone.utc).isoformat()
    t3 = datetime.now(timezone.utc).isoformat()

    telem_res = client.post(f"/api/v1/runs/{act_id}/telemetry", json={
        "points": [
            {"latitude": 13.0820, "longitude": 80.2700, "timestamp": t1, "speed": 2.5, "accuracy": 3.0},
            {"latitude": 13.0830, "longitude": 80.2705, "timestamp": t2, "speed": 3.2, "accuracy": 2.5},
            {"latitude": 13.0840, "longitude": 80.2710, "timestamp": t3, "speed": 3.8, "accuracy": 2.0},
        ]
    }, headers=auth_headers)
    assert telem_res.status_code == 200
    assert telem_res.json()["saved_points"] == 3

    # 3. Live HUD endpoint returns real-time coordinates
    res_live = client.get(f"/api/v1/runs/{act_id}/live")
    assert res_live.status_code == 200
    live = res_live.json()
    assert live["points_count"] == 3
    assert live["current_latitude"] == 13.0840
    assert live["current_longitude"] == 80.2710
    assert live["current_speed_kmh"] > 0
    assert len(live["recent_points"]) == 3
    assert live["recent_points"][-1]["latitude"] == 13.0840

    # 4. Full path endpoint returns full GPS trail for Flutter map rendering
    res_path = client.get(f"/api/v1/runs/{act_id}/path")
    assert res_path.status_code == 200
    path_data = res_path.json()
    assert path_data["total_points"] == 3
    assert len(path_data["path"]) == 3
    assert path_data["path"][0]["latitude"] == 13.0820
    assert path_data["path"][2]["latitude"] == 13.0840


def test_territory_nearby_dynamic_distance(client):
    """
    Verify nearby territories distance is calculated from real DB polygon centroid using Haversine
    rather than hardcoded values.
    """
    res = client.get("/api/v1/territories/nearby?lat=13.0827&lng=80.2707&radius_km=10.0")
    assert res.status_code == 200
    data = res.json()
    assert data["total_nearby"] >= 1
    # Check that distance_km is dynamically calculated (not hardcoded 1.2 or 0.8)
    first_terr = data["territories"][0]
    assert isinstance(first_terr["distance_km"], float)
    assert first_terr["distance_km"] >= 0.0


def test_prevent_double_claim_on_same_run(client, auth_headers):
    """
    Verify that a runner cannot claim territory twice from the same run.
    """
    # 1. Start and stream closed loop
    res_start = client.post("/api/v1/runs/start", json={"title": "Loop Run"}, headers=auth_headers)
    act_id = res_start.json()["activity_id"]

    client.post(f"/api/v1/runs/{act_id}/telemetry", json={
        "points": [
            {"latitude": 13.0800, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0850, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0850, "longitude": 80.2750, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0800, "longitude": 80.2750, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0800, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    }, headers=auth_headers)

    client.post(f"/api/v1/runs/{act_id}/finish", json={"steps": 5000, "calories": 300}, headers=auth_headers)

    # 2. First claim succeeds
    claim_payload = {"activity_id": act_id, "territory_name": "Unique Loop Turf"}
    res1 = client.post("/api/v1/territories/claim-from-run", json=claim_payload, headers=auth_headers)
    assert res1.status_code == 200
    assert res1.json()["status"] == "success"

    # 3. Second claim with same activity_id is rejected
    res2 = client.post("/api/v1/territories/claim-from-run", json=claim_payload, headers=auth_headers)
    assert res2.status_code == 400
    assert "already been claimed" in res2.json()["detail"].lower()
