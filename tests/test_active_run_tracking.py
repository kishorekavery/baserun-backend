from datetime import datetime, timezone
import uuid


def test_screen_6_home_map(client):
    """Screen 6: Home Map - View colored territories and ready to run card"""
    res = client.get("/api/v1/map/home?user_id=1")
    assert res.status_code == 200
    data = res.json()
    assert "active_runners_nearby" in data
    assert "user_territories_count" in data
    assert "club_territories_count" in data
    assert len(data["territories"]) >= 1


def test_screen_6_start_run(client, auth_headers):
    """Screen 6: Home Map - 'Start Run' button initializes session"""
    res = client.post("/api/v1/runs/start", json={
        "title": "Marina Beach Loop"
    }, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "in_progress"
    assert "activity_id" in data
    assert data["title"] == "Marina Beach Loop"


def test_screen_7_run_in_progress_flow(client, auth_headers):
    """Screen 7: Run In Progress - Telemetry, Live HUD, Pause and Resume"""
    # 1. Start run
    res_start = client.post("/api/v1/runs/start", json={"title": "Morning Run"}, headers=auth_headers)
    act_id = res_start.json()["activity_id"]

    # 2. Stream telemetry points
    telemetry_payload = {
        "activity_id": act_id,
        "points": [
            {"latitude": 13.0827, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat(), "speed": 2.8, "accuracy": 4.0},
            {"latitude": 13.0837, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat(), "speed": 3.0, "accuracy": 4.0},
            {"latitude": 13.0837, "longitude": 80.2717, "timestamp": datetime.now(timezone.utc).isoformat(), "speed": 3.1, "accuracy": 4.0},
        ]
    }
    res_telem = client.post(f"/api/v1/runs/{act_id}/telemetry", json=telemetry_payload, headers=auth_headers)
    assert res_telem.status_code == 200
    assert res_telem.json()["saved_points"] == 3

    # 3. Live HUD stats
    res_live = client.get(f"/api/v1/runs/{act_id}/live")
    assert res_live.status_code == 200
    live_data = res_live.json()
    assert live_data["status"] == "in_progress"
    assert "distance_km" in live_data
    assert "duration_formatted" in live_data
    assert "pace_formatted" in live_data

    # 4. Pause run
    res_pause = client.post(f"/api/v1/runs/{act_id}/pause", headers=auth_headers)
    assert res_pause.status_code == 200
    assert res_pause.json()["status"] == "paused"

    # 5. Resume run
    res_resume = client.post(f"/api/v1/runs/{act_id}/resume", headers=auth_headers)
    assert res_resume.status_code == 200
    assert res_resume.json()["status"] == "in_progress"


def test_screen_8_run_summary_finish(client, auth_headers):
    """Screen 8: Run Summary - Review run with steps, calories, elevation"""
    # 1. Start run
    res_start = client.post("/api/v1/runs/start", json={"title": "Marina Beach Loop"}, headers=auth_headers)
    act_id = res_start.json()["activity_id"]

    # 2. Finish run
    finish_payload = {
        "steps": 6982,
        "calories": 412,
        "elevation_gain_m": 12.0
    }
    res_finish = client.post(f"/api/v1/runs/{act_id}/finish", json=finish_payload, headers=auth_headers)
    assert res_finish.status_code == 200
    summary = res_finish.json()
    assert summary["status"] == "completed"
    assert summary["steps"] == 6982
    assert summary["calories"] == 412
    assert summary["elevation_gain_m"] == 12.0
    assert summary["avg_speed_kmh"] > 0
    assert summary["title"] == "Marina Beach Loop"

    # 3. Get summary card
    res_get = client.get(f"/api/v1/runs/{act_id}/summary")
    assert res_get.status_code == 200
    assert res_get.json()["steps"] == 6982
