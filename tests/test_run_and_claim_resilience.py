from datetime import datetime, timezone

def test_claim_territory_with_offline_run_and_client_points(client, auth_headers):
    # 1. Activity ID generated on client while offline (never sent to /runs/start)
    offline_activity_id = f"act_offline_{datetime.now().timestamp()}"
    now_iso = datetime.now(timezone.utc).isoformat()
    client_breadcrumbs = [
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": now_iso, "speed": 3.0, "accuracy": 3.0},
        {"latitude": 13.0835, "longitude": 80.2700, "timestamp": now_iso, "speed": 3.2, "accuracy": 3.0},
        {"latitude": 13.0835, "longitude": 80.2715, "timestamp": now_iso, "speed": 3.1, "accuracy": 3.0},
        {"latitude": 13.0820, "longitude": 80.2715, "timestamp": now_iso, "speed": 3.0, "accuracy": 3.0},
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": now_iso, "speed": 2.9, "accuracy": 3.0}
    ]

    # 2. Call finish_run directly with client breadcrumbs
    finish_res = client.post(
        f"/api/v1/runs/{offline_activity_id}/finish",
        json={
            "steps": 4500,
            "calories": 250,
            "distance_km": 1.25,
            "points": client_breadcrumbs
        },
        headers=auth_headers
    )
    assert finish_res.status_code == 200
    summary = finish_res.json()
    assert summary["activity_id"] == offline_activity_id
    assert summary["can_claim_territory"] is True

    # 3. Call claim_territory_from_run directly
    claim_res = client.post(
        "/api/v1/territories/claim-from-run",
        json={
            "activity_id": offline_activity_id,
            "territory_name": "Auto Recovered Zone",
            "points": client_breadcrumbs
        },
        headers=auth_headers
    )
    assert claim_res.status_code == 200
    claim_data = claim_res.json()
    assert claim_data["status"] == "success"
    assert claim_data["territory_name"] == "Auto Recovered Zone"
    assert claim_data["area_km2"] > 0
    assert "badge_earned" in claim_data
