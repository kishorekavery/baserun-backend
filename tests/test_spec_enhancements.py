"""
Coverage for UI_BACKEND_ENHANCEMENTS.md requirements and the bugs fixed with them.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import db_run, register, square_loop


def offline_claim(client, user, loop, active_seconds, name="Loop"):
    """Finish an offline run (client-generated id) and claim it."""
    act_id = f"act_{uuid.uuid4().hex[:10]}"
    finish = client.post(
        f"/api/v1/runs/{act_id}/finish",
        json={"points": loop, "active_seconds": active_seconds, "total_steps": 900},
        headers=user["headers"],
    )
    assert finish.status_code == 200, finish.text
    res = client.post(
        "/api/v1/territories/claim-from-run",
        json={"activity_id": act_id, "territory_name": name},
        headers=user["headers"],
    )
    assert res.status_code == 200, res.text
    return act_id, res.json()


# ---------------------------------------------------------------- auth / identity

def test_login_with_email_case_insensitive_returns_profile(client):
    user = register(client, "Mixed")
    res = client.post("/auth/login", json={
        "username": user["payload"]["email"].upper(), "password": "Password123!",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == user["user_id"]
    assert data["username"] == user["payload"]["username"]
    assert data["territory_color"] == "#00E5FF"
    assert data["token"] == data["access_token"]
    assert data["token_type"] == "bearer"
    assert "club_id" in data and "club_name" in data

    res = client.post("/auth/login", json={
        "username": user["payload"]["username"].lower(), "password": "Password123!",
    })
    assert res.status_code == 200


def test_register_rejects_case_insensitive_duplicates_and_bad_input(client):
    user = register(client, "dupe")
    res = client.post("/auth/register", json={
        "username": user["payload"]["username"].upper(),
        "email": f"other_{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!",
    })
    assert res.status_code == 400

    res = client.post("/auth/register", json={
        "username": "bad name!", "email": "x@example.com", "password": "Password123!",
    })
    assert res.status_code == 422

    res = client.post("/auth/register", json={
        "username": f"ok_{uuid.uuid4().hex[:6]}", "email": f"{uuid.uuid4().hex[:6]}@example.com",
        "password": "Password123!", "confirm_password": "Different123!",
    })
    assert res.status_code == 422


def test_check_username_is_case_insensitive_and_validates(client):
    user = register(client, "Checker")
    res = client.get(f"/auth/check-username?username={user['payload']['username'].upper()}")
    assert res.json()["available"] is False
    res = client.get("/auth/check-username?username=a b")
    assert res.status_code == 200
    assert res.json()["available"] is False


def test_identity_validates_hex_color(client):
    user = register(client, "color")
    res = client.put("/users/identity", json={"territory_color": "blue"}, headers=user["headers"])
    assert res.status_code == 422
    assert "hex color" in str(res.json()["detail"])
    assert res.json()["code"] == "VALIDATION_ERROR"


def test_color_change_cascades_to_owned_territories(client):
    user = register(client, "painter")
    loop = square_loop()
    offline_claim(client, user, loop, 400)
    client.put("/users/identity", json={"territory_color": "#76ff03", "bio": "Hi"}, headers=user["headers"])
    res = client.get(
        f"/api/v1/map/home?lat={loop[0]['latitude']}&lng={loop[0]['longitude']}&radius_km=1",
        headers=user["headers"],
    )
    mine = [t for t in res.json()["territories"] if t["is_user_territory"]]
    assert mine and all(t["owner_color"] == "#76FF03" for t in mine)


def test_identity_club_join_and_explicit_leave_moves_territories(client):
    user = register(client, "clubber")
    _, claim = offline_claim(client, user, square_loop(), 400)
    clubs = client.get("/api/v1/clubs").json()["clubs"]
    club_id = clubs[0]["id"]

    res = client.put("/users/identity", json={"club_id": club_id}, headers=user["headers"])
    assert res.status_code == 200
    assert res.json()["club_id"] == club_id
    territory = client.get(f"/api/v1/territories/{claim['territory_id']}").json()
    assert territory["club_id"] == club_id

    # Omitting club_id keeps the club
    client.put("/users/identity", json={"bio": "still here"}, headers=user["headers"])
    assert client.get("/users/me", headers=user["headers"]).json()["club_id"] == club_id

    # Explicit null leaves
    res = client.put("/users/identity", json={"club_id": None}, headers=user["headers"])
    assert res.json()["club_id"] is None
    assert client.get(f"/api/v1/territories/{claim['territory_id']}").json()["club_id"] is None


def test_put_user_by_id_is_owner_only(client):
    a, b = register(client, "pa"), register(client, "pb")
    assert client.put(f"/users/{b['user_id']}", json={"bio": "x"}, headers=a["headers"]).status_code == 403
    assert client.put(f"/users/{a['user_id']}", json={"bio": "x"}, headers=a["headers"]).status_code == 200


def test_social_login_reuses_account(client):
    email = f"social_{uuid.uuid4().hex[:6]}@gmail.com"
    payload = {"provider": "google", "token": "t1", "email": email, "name": "Social Runner"}
    first = client.post("/auth/social-login", json=payload).json()
    second = client.post("/auth/social-login", json={**payload, "token": "t2"}).json()
    assert first["user_id"] == second["user_id"]
    assert first["is_new_user"] is True and second["is_new_user"] is False


def test_social_login_uses_verified_identity(client, monkeypatch):
    from routers import auth_ext
    from utils.social_auth import SocialAuthError, SocialIdentity

    monkeypatch.setenv("SOCIAL_LOGIN_ALLOW_UNVERIFIED", "false")
    victim = register(client, "victim")

    async def reject(provider, token):
        raise SocialAuthError("Invalid Google token")

    monkeypatch.setattr(auth_ext, "verify_social_token", reject)
    res = client.post("/auth/social-login", json={
        "provider": "google", "token": "forged", "email": victim["payload"]["email"],
    })
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"

    verified_email = f"verified_{uuid.uuid4().hex[:6]}@gmail.com"

    async def accept(provider, token):
        return SocialIdentity("google", "sub-123", verified_email, "Verified Runner")

    monkeypatch.setattr(auth_ext, "verify_social_token", accept)
    res = client.post("/auth/social-login", json={
        "provider": "google", "token": "real", "email": victim["payload"]["email"],
    })
    assert res.status_code == 200
    assert res.json()["email"] == verified_email
    assert res.json()["user_id"] != victim["user_id"]


def test_error_responses_include_code(client):
    res = client.get("/users/me")
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"
    res = client.get("/api/v1/territories/999999")
    assert res.json() == {"detail": "Territory not found", "code": "NOT_FOUND"}


# ---------------------------------------------------------------- runs

def test_telemetry_accuracy_filter_dedup_and_loop_closure(client):
    user = register(client, "tele")
    act_id = client.post("/api/v1/runs/start", json={
        "route_mode": "free_run", "start_latitude": 13.0, "start_longitude": 80.0, "device_info": "Pixel 7",
    }, headers=user["headers"]).json()["activity_id"]

    loop = square_loop()
    noisy = dict(loop[1], accuracy=31.0, timestamp=loop[0]["timestamp"])
    first = client.post(f"/api/v1/runs/{act_id}/telemetry", json={"points": [loop[0], noisy, loop[1]]}, headers=user["headers"]).json()
    assert first["points_ingested"] == 2
    assert first["is_loop_closed"] is False

    # Retried batch is ignored, distance unchanged
    retry = client.post(f"/api/v1/runs/{act_id}/telemetry", json={"points": [loop[0], loop[1]]}, headers=user["headers"]).json()
    assert retry["points_ingested"] == 0
    assert retry["total_distance_km"] == first["total_distance_km"]

    rest = client.post(
        f"/api/v1/runs/{act_id}/telemetry", json={"points": loop[2:], "steps": 500, "active_seconds": 240},
        headers=user["headers"],
    ).json()
    assert rest["is_loop_closed"] is True
    assert rest["total_distance_km"] == pytest.approx(0.66, abs=0.03)

    live = client.get(f"/api/v1/runs/{act_id}/live", headers=user["headers"]).json()
    assert live["is_loop_closed"] is True
    assert live["points_count"] == 5

    client.post(f"/api/v1/runs/{act_id}/finish", json={}, headers=user["headers"])
    late = client.post(f"/api/v1/runs/{act_id}/telemetry", json={"points": loop[:1]}, headers=user["headers"])
    assert late.status_code == 409


def test_pause_time_is_excluded_and_finish_is_idempotent(client):
    user = register(client, "pauser")
    act_id = client.post("/api/v1/runs/start", json={}, headers=user["headers"]).json()["activity_id"]
    client.post(f"/api/v1/runs/{act_id}/telemetry", json={"points": square_loop()}, headers=user["headers"])

    # 30 min since start, of which 20 min paused
    now = datetime.now(timezone.utc)
    db_run(
        "UPDATE runs SET start_time = $2, status = 'paused', paused_at = $3 WHERE activity_id = $1",
        act_id, now - timedelta(minutes=30), now - timedelta(minutes=20),
    )
    assert client.post(f"/api/v1/runs/{act_id}/resume", headers=user["headers"]).json()["status"] == "recording"

    summary = client.post(f"/api/v1/runs/{act_id}/finish", json={"total_steps": 1234}, headers=user["headers"]).json()
    assert 595 <= summary["duration_seconds"] <= 610
    assert summary["duration_formatted"].startswith("10:0")
    assert summary["steps"] == 1234
    assert summary["is_loop_closed"] is True
    assert summary["can_claim_territory"] is True
    assert summary["suggested_area_km2"] > 0
    assert "First Steps" in summary["badges_earned"]

    again = client.post(f"/api/v1/runs/{act_id}/finish", json={}, headers=user["headers"]).json()
    assert again["duration_seconds"] == summary["duration_seconds"]
    assert again["badges_earned"] == []

    stored = client.get(f"/api/v1/runs/{act_id}/summary", headers=user["headers"]).json()
    assert stored["polygon_wkt"] == summary["polygon_wkt"]

    assert client.post(f"/api/v1/runs/{act_id}/pause", headers=user["headers"]).status_code == 409


def test_open_run_cannot_be_claimed(client):
    user = register(client, "open")
    act_id = client.post("/api/v1/runs/start", json={}, headers=user["headers"]).json()["activity_id"]
    line = square_loop()[:3]  # never returns to the start
    client.post(f"/api/v1/runs/{act_id}/telemetry", json={"points": line}, headers=user["headers"])
    summary = client.post(f"/api/v1/runs/{act_id}/finish", json={}, headers=user["headers"]).json()
    assert summary["is_loop_closed"] is False
    assert summary["can_claim_territory"] is False

    res = client.post("/api/v1/territories/claim-from-run", json={"activity_id": act_id}, headers=user["headers"])
    assert res.status_code == 400
    assert "loop was not closed" in res.json()["detail"].lower()


def test_run_reads_are_owner_only(client):
    a, b = register(client, "ra"), register(client, "rb")
    act_id = client.post("/api/v1/runs/start", json={}, headers=a["headers"]).json()["activity_id"]
    for path in ("live", "path", "summary"):
        assert client.get(f"/api/v1/runs/{act_id}/{path}").status_code == 401
        assert client.get(f"/api/v1/runs/{act_id}/{path}", headers=b["headers"]).status_code == 403
        assert client.get(f"/api/v1/runs/{act_id}/{path}", headers=a["headers"]).status_code == 200


def test_duplicate_client_activity_id_on_start(client):
    user = register(client, "dupact")
    act_id = f"act_{uuid.uuid4().hex[:8]}"
    assert client.post("/api/v1/runs/start", json={"activity_id": act_id}, headers=user["headers"]).status_code == 200
    assert client.post("/api/v1/runs/start", json={"activity_id": act_id}, headers=user["headers"]).status_code == 409


def test_legacy_telemetry_without_activity_id(client):
    user = register(client, "legacy")
    res = client.post("/api/v1/bases/telemetry", json={"points": square_loop()[:2]}, headers=user["headers"])
    assert res.status_code == 200
    assert res.json()["saved_points"] == 2
    assert res.json()["activity_id"].startswith("act_")


# ---------------------------------------------------------------- territories

def test_fortify_takeover_and_failed_challenge(client):
    alice, bob, carol = register(client, "alice"), register(client, "bob"), register(client, "carol")
    loop = square_loop()

    _, claim = offline_claim(client, alice, loop, active_seconds=1800, name="Marina Circuit")
    assert claim["status"] == "success"
    assert claim["badge_earned"] == "First Conquest"
    assert "on BASER" in claim["share_text"]
    territory_id = claim["territory_id"]

    _, fortified = offline_claim(client, alice, loop, active_seconds=1700)
    assert fortified["status"] == "fortified"
    assert fortified["territory_id"] == territory_id
    assert fortified["defense_level"] == 2

    challenge = client.post(f"/api/v1/territories/{territory_id}/challenge", json={"challenger_id": 1}, headers=bob["headers"])
    assert challenge.status_code == 200
    challenge = challenge.json()
    assert challenge["challenger_id"] == str(bob["user_id"])
    assert challenge["target_pace"] != "0'00\""
    same = client.post(f"/api/v1/territories/{territory_id}/challenge", headers=bob["headers"]).json()
    assert same["challenge_id"] == challenge["challenge_id"]
    assert client.post(f"/api/v1/territories/{territory_id}/challenge", headers=alice["headers"]).status_code == 400

    _, failed = offline_claim(client, carol, loop, active_seconds=3600)
    assert failed["status"] == "challenge_failed"
    assert failed["owner_username"] == alice["payload"]["username"]

    _, taken = offline_claim(client, bob, loop, active_seconds=300, name="Bob's Turf")
    assert taken["status"] == "takeover"
    assert taken["owner_username"] == bob["payload"]["username"]
    assert "Hostile Takeover" in taken["badges_earned"]

    details = client.get(f"/api/v1/territories/{territory_id}", headers=alice["headers"]).json()
    assert details["owner_id"] == str(bob["user_id"])
    assert details["can_challenge"] is True
    assert details["is_user_territory"] is False
    assert details["times_challenged"] == 2
    status = db_run("SELECT status FROM challenges WHERE challenge_id = $1", challenge["challenge_id"], fetch="fetchval")
    assert status == "won"


def test_claim_area_is_computed_server_side(client):
    user = register(client, "honest")
    act_id = f"act_{uuid.uuid4().hex[:10]}"
    loop = square_loop(size_deg=0.0015)
    client.post(f"/api/v1/runs/{act_id}/finish", json={"points": loop}, headers=user["headers"])
    res = client.post("/api/v1/territories/claim-from-run", json={
        "activity_id": act_id, "area_km2": 99.0, "perimeter_km": 99.0,
    }, headers=user["headers"]).json()
    assert 0.025 < res["area_km2"] < 0.03
    assert 0.6 < res["perimeter_km"] < 0.7


def test_defense_level_decays_weekly(client):
    user = register(client, "decay")
    _, claim = offline_claim(client, user, square_loop(), 400)
    db_run(
        "UPDATE territories SET defense_level = 3, last_defended_at = $2 WHERE id = $1",
        claim["territory_id"], datetime.now(timezone.utc) - timedelta(days=15),
    )
    assert client.get(f"/api/v1/territories/{claim['territory_id']}").json()["defense_level"] == 1


def test_nearby_excludes_far_territories(client):
    user = register(client, "near")
    loop = square_loop()
    offline_claim(client, user, loop, 400)
    lat, lng = loop[0]["latitude"], loop[0]["longitude"]

    inside = client.get(f"/api/v1/territories/nearby?lat={lat + 0.0007}&lng={lng + 0.0007}&radius_km=1").json()
    assert inside["count"] >= 1
    assert inside["territories"][0]["distance_meters"] == 0

    far = client.get(f"/api/v1/territories/nearby?lat={lat + 1.5}&lng={lng + 1.5}&radius_km=1").json()
    assert all(t["owner_id"] != str(user["user_id"]) for t in far["territories"])


def test_home_map_bbox_flags_and_active_runners(client):
    user = register(client, "mapper")
    loop = square_loop()
    _, claim = offline_claim(client, user, loop, 400)
    lat, lng = loop[0]["latitude"], loop[0]["longitude"]

    act_id = client.post("/api/v1/runs/start", json={}, headers=user["headers"]).json()["activity_id"]
    client.post(f"/api/v1/runs/{act_id}/telemetry", json={"points": square_loop(lat, lng)[:1]}, headers=user["headers"])

    bbox = f"min_lat={lat - 0.01}&min_lng={lng - 0.01}&max_lat={lat + 0.01}&max_lng={lng + 0.01}"
    data = client.get(f"/api/v1/map/home?{bbox}", headers=user["headers"]).json()
    mine = next(t for t in data["territories"] if t["id"] == claim["territory_id"])
    assert mine["is_user_territory"] is True
    assert mine["polygon_wkt"].startswith("POLYGON")
    assert data["user_territories_count"] >= 1
    assert data["active_runners_nearby"] >= 1

    outside = client.get("/api/v1/map/home?min_lat=-10&min_lng=-10&max_lat=-9&max_lng=-9", headers=user["headers"]).json()
    assert outside["territories"] == []

    assert client.get(f"/api/v1/map/home?min_lat={lat}").status_code == 422


# ---------------------------------------------------------------- clubs & profile

def test_club_join_switch_leave(client):
    user = register(client, "switch")
    _, claim = offline_claim(client, user, square_loop(), 400)
    clubs = client.get("/api/v1/clubs").json()["clubs"]
    first, second = clubs[0]["id"], clubs[1]["id"]

    res = client.post(f"/api/v1/clubs/{first}/join", headers=user["headers"]).json()
    assert res["territories_transferred"] == 1
    res = client.post(f"/api/v1/clubs/{second}/join", headers=user["headers"]).json()
    assert res["club_id"] == second
    listing = {c["id"]: c for c in client.get("/api/v1/clubs", headers=user["headers"]).json()["clubs"]}
    assert listing[second]["is_joined"] and not listing[first]["is_joined"]
    assert listing[second]["territory_count"] >= 1
    assert listing[second]["total_area_km2"] > 0

    assert client.post(f"/api/v1/clubs/{first}/leave", headers=user["headers"]).status_code == 400
    assert client.post(f"/api/v1/clubs/{second}/leave", headers=user["headers"]).json()["club_id"] is None
    assert client.get(f"/api/v1/territories/{claim['territory_id']}").json()["club_id"] is None
    assert client.post("/api/v1/clubs/999999/join", headers=user["headers"]).status_code == 404


def test_profile_stats_privacy_and_history(client):
    user, other = register(client, "prof"), register(client, "peek")
    offline_claim(client, user, square_loop(), 400, name="Home Turf")

    me = client.get("/users/me", headers=user["headers"]).json()
    assert me["email"] == user["payload"]["email"]
    assert me["stats"]["total_runs"] == 1
    assert me["stats"]["bases_claimed"] == 1
    assert me["stats"]["bases_held"] == 1
    assert me["favorite_base"] == "Home Turf"
    assert me["club_name"] is None

    public = client.get(f"/users/{user['user_id']}/profile", headers=other["headers"]).json()
    assert public["email"] is None
    assert client.get(f"/users/{user['user_id']}/profile").json()["email"] is None

    runs = client.get(f"/users/{user['user_id']}/runs?limit=1", headers=user["headers"]).json()
    assert runs["total_runs"] == 1 and runs["limit"] == 1
    assert runs["runs"][0]["badge"] == "Claimed Base"

    achievements = client.get(f"/users/{user['user_id']}/achievements", headers=other["headers"]).json()
    titles = {a["title"] for a in achievements["achievements"]}
    assert {"First Steps", "First Conquest"} <= titles
    fresh = client.get(f"/users/{other['user_id']}/achievements", headers=other["headers"]).json()
    assert fresh["total_badges"] == 0
    assert client.get("/users/abc/achievements", headers=other["headers"]).status_code == 422


# ---------------------------------------------------------------- resilience

def test_stale_token_on_public_endpoint_is_anonymous(client):
    user = register(client, "stale")
    client.post("/auth/logout", headers=user["headers"])
    res = client.get("/api/v1/clubs", headers=user["headers"])
    assert res.status_code == 200
    assert not any(c["is_joined"] for c in res.json()["clubs"])
    assert client.get("/users/me", headers=user["headers"]).status_code == 401


def test_database_outage_returns_503(monkeypatch):
    from fastapi.testclient import TestClient

    import db.database as database
    from main import app

    async def unreachable(config):
        raise OSError("connection refused")

    monkeypatch.setattr(database, "_create_pool", unreachable)
    monkeypatch.setattr(database._state, "pool", None)
    monkeypatch.setattr(database._state, "loop", None)
    with TestClient(app, raise_server_exceptions=False) as outage_client:
        res = outage_client.get("/api/v1/clubs")
        assert res.status_code == 503
        assert res.json()["code"] == "SERVICE_UNAVAILABLE"
        health = outage_client.get("/health/db")
        assert health.status_code == 503
        assert health.json()["connection"] == "error"
