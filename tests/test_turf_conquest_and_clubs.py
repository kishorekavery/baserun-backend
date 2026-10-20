from datetime import datetime, timezone
import uuid


def test_screen_9_claim_territory_from_run(client, auth_headers):
    """Screen 9: Territory Claimed - Success Screen"""
    # Start and finish run
    res_start = client.post("/api/v1/runs/start", json={"title": "Marina Beach Loop"}, headers=auth_headers)
    act_id = res_start.json()["activity_id"]

    # Stream loop coordinates
    client.post(f"/api/v1/runs/{act_id}/telemetry", json={
        "activity_id": act_id,
        "points": [
            {"latitude": 13.0827, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0837, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0837, "longitude": 80.2717, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0827, "longitude": 80.2717, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0827, "longitude": 80.2707, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    }, headers=auth_headers)
    client.post(f"/api/v1/runs/{act_id}/finish", json={"steps": 6982, "calories": 412}, headers=auth_headers)

    # Submit for territory claim
    claim_payload = {
        "activity_id": act_id,
        "territory_name": "Marina Beach (North)"
    }
    res = client.post("/api/v1/territories/claim-from-run", json=claim_payload, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["territory_name"] == "Marina Beach (North)"
    assert data["area_km2"] > 0
    assert "share_text" in data
    assert "#RunToClaim" in data["share_text"]


def test_screen_10_territory_details_and_challenge(client, auth_headers):
    """Screen 10: Territory Details and Challenge button"""
    # 0. Ensure at least one claimed base exists
    base_res = client.post("/api/v1/bases/claim", json={
        "points": [
            {"latitude": 13.0820, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0830, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0830, "longitude": 80.2710, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0820, "longitude": 80.2710, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"latitude": 13.0820, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    }, headers=auth_headers)
    assert base_res.status_code == 200
    territory_id = base_res.json()["base_id"]

    # 1. Get details for territory
    res = client.get(f"/api/v1/territories/{territory_id}")
    assert res.status_code == 200
    data = res.json()
    assert "area_km2" in data
    assert "total_runs" in data
    assert "total_km" in data
    assert "days_active" in data

    # 2. Challenge owner
    res_chal = client.post(f"/api/v1/territories/{territory_id}/challenge", json={}, headers=auth_headers)
    assert res_chal.status_code == 200
    chal_data = res_chal.json()
    assert chal_data["status"] == "challenge_active"
    assert "instructions" in chal_data


def test_screen_11_clubs_and_leaderboard(client, auth_headers):
    """Screen 11: Clubs and Leaderboard top contributors"""
    # 1. List clubs
    res = client.get("/api/v1/clubs")
    assert res.status_code == 200
    clubs = res.json()["clubs"]
    assert len(clubs) >= 1
    club_names = [c["name"] for c in clubs]
    assert "Chennai Runners" in club_names

    # 2. Join club
    res_join = client.post("/api/v1/clubs/1/join", headers=auth_headers)
    assert res_join.status_code == 200
    assert "Successfully joined" in res_join.json()["message"]

    # 3. Club Leaderboard
    res_lb = client.get("/api/v1/clubs/1/leaderboard?current_user_id=1")
    assert res_lb.status_code == 200
    lb_data = res_lb.json()
    assert len(lb_data["leaderboard"]) == 5
    # Check top ranks match design: runvicky (#1), priya_r (#2), adventurer99 (#3)
    usernames = [m["username"] for m in lb_data["leaderboard"]]
    assert "runvicky" in usernames
    assert "priya_r" in usernames
    assert "adventurer99" in usernames


def test_screen_12_profile_and_stats(client, auth_headers):
    """Screen 12: Profile, My Runs, My Territories, and Achievements"""
    # 1. My Full Profile
    res_prof = client.get("/api/v1/users/profile", headers=auth_headers)
    assert res_prof.status_code == 200
    prof = res_prof.json()
    assert "total_territories" in prof
    assert "total_km" in prof
    assert "total_runs" in prof
    assert "city" in prof

    # 2. My Runs
    user_id = str(prof["id"])
    res_runs = client.get(f"/api/v1/users/{user_id}/runs", headers=auth_headers)
    assert res_runs.status_code == 200
    assert "runs" in res_runs.json()

    # 3. My Territories
    res_terrs = client.get(f"/api/v1/users/{user_id}/territories")
    assert res_terrs.status_code == 200
    assert "bases" in res_terrs.json()

    # 4. Achievements (12 Badges)
    res_ach = client.get(f"/api/v1/users/{user_id}/achievements")
    assert res_ach.status_code == 200
    assert res_ach.json()["total_badges"] >= 1
