import uuid
from datetime import datetime, timezone


def test_record_telemetry_success(client, auth_headers):
    activity_id = str(uuid.uuid4())
    payload = {
        "activity_id": activity_id,
        "points": [
            {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 5.0,
                "speed": 3.2,
                "altitude": 10.5
            },
            {
                "latitude": 37.7750,
                "longitude": -122.4195,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 6.0,
                "speed": 3.4,
                "altitude": 11.0
            }
        ]
    }
    res = client.post("/api/v1/bases/telemetry", json=payload, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["saved_points"] == 2
    assert data["total_received"] == 2


def test_record_telemetry_filters_inaccurate_gps(client, auth_headers):
    activity_id = str(uuid.uuid4())
    payload = {
        "activity_id": activity_id,
        "points": [
            {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 5.0,  # Valid
                "speed": 3.0
            },
            {
                "latitude": 37.7750,
                "longitude": -122.4195,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 55.0,  # INACCURATE (> 20.0m) -> Should be filtered out
                "speed": 3.0
            }
        ]
    }
    res = client.post("/api/v1/bases/telemetry", json=payload, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["saved_points"] == 1
    assert data["total_received"] == 2


def test_record_telemetry_filters_high_speed_anticheat(client, auth_headers):
    activity_id = str(uuid.uuid4())
    payload = {
        "activity_id": activity_id,
        "points": [
            {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 5.0,
                "speed": 4.5  # Valid runner speed
            },
            {
                "latitude": 37.7750,
                "longitude": -122.4195,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 5.0,
                "speed": 35.0  # Vehicle speed (> 27.78 m/s, ~126 km/h) -> Should be filtered out
            }
        ]
    }
    res = client.post("/api/v1/bases/telemetry", json=payload, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["saved_points"] == 1
    assert data["total_received"] == 2


def test_get_activity_telemetry_success(client, auth_headers):
    activity_id = str(uuid.uuid4())
    post_payload = {
        "activity_id": activity_id,
        "points": [
            {
                "latitude": 13.0827,
                "longitude": 80.2707,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 4.0,
                "speed": 3.1
            },
            {
                "latitude": 13.0828,
                "longitude": 80.2708,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": 4.0,
                "speed": 3.2
            }
        ]
    }
    client.post("/api/v1/bases/telemetry", json=post_payload, headers=auth_headers)

    # Fetch recorded telemetry for this activity
    res = client.get(f"/api/v1/bases/telemetry/{activity_id}", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["activity_id"] == activity_id
    assert data["total_points"] == 2
    assert len(data["points"]) == 2
    assert data["points"][0]["latitude"] == 13.0827


def test_get_activity_telemetry_requires_auth(client):
    res = client.get(f"/api/v1/bases/telemetry/{uuid.uuid4()}")
    assert res.status_code == 401


def test_get_activity_telemetry_not_found(client, auth_headers):
    random_id = str(uuid.uuid4())
    res = client.get(f"/api/v1/bases/telemetry/{random_id}", headers=auth_headers)
    assert res.status_code == 404
