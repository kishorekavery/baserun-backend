"""
Change password, connected apps and account deletion.
"""
import io
import uuid

from PIL import Image

from tests.conftest import db_run, register, square_loop
from utils import social_auth
from utils.social_auth import SocialAuthError, SocialIdentity


def social_login(client, provider="google", email=None):
    email = email or f"social_{uuid.uuid4().hex[:8]}@example.com"
    response = client.post("/auth/social-login", json={"provider": provider, "token": "t", "email": email, "name": "Sam"})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"headers": {"Authorization": f"Bearer {token}"}, "user_id": response.json()["user_id"], "email": email}


def login(client, user, password):
    return client.post("/auth/login", json={"username": user["payload"]["username"], "password": password})


def change(client, user, current, new):
    return client.post("/auth/change-password", json={"current_password": current, "new_password": new},
                       headers=user["headers"])


def claim_run(client, user, points, name=None):
    """Runs a loop and claims it; returns the claim response body."""
    activity = client.post("/api/v1/runs/start", json={"title": "Loop"}, headers=user["headers"]).json()["activity_id"]
    client.post(f"/api/v1/runs/{activity}/telemetry", json={"activity_id": activity, "points": points},
                headers=user["headers"])
    client.post(f"/api/v1/runs/{activity}/finish", json={}, headers=user["headers"])
    payload = {"activity_id": activity}
    if name:
        payload["territory_name"] = name
    response = client.post("/api/v1/territories/claim-from-run", json=payload, headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def tiny_jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(output, format="JPEG")
    return output.getvalue()


# ---- Change password

def test_change_password_replaces_the_old_one_and_keeps_this_session(client):
    user = register(client, "pw")
    response = change(client, user, "Password123!", "NewPassword456!")
    assert response.status_code == 200, response.text
    assert client.get("/users/me", headers=user["headers"]).status_code == 200
    assert login(client, user, "Password123!").status_code == 401
    assert login(client, user, "NewPassword456!").status_code == 200


def test_change_password_signs_out_other_devices(client, monkeypatch):
    monkeypatch.setenv("MAX_SESSIONS_PER_USER", "2")
    user = register(client, "pw")
    other = {"Authorization": f"Bearer {login(client, user, 'Password123!').json()['access_token']}"}
    assert client.get("/users/me", headers=other).status_code == 200

    assert change(client, user, "Password123!", "NewPassword456!").status_code == 200
    assert client.get("/users/me", headers=other).status_code == 401
    assert client.get("/users/me", headers=user["headers"]).status_code == 200


def test_wrong_current_password_is_400_so_the_app_does_not_log_out(client):
    user = register(client, "pw")
    response = change(client, user, "wrong-password", "NewPassword456!")
    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is incorrect"
    assert login(client, user, "Password123!").status_code == 200


def test_new_password_rules(client):
    user = register(client, "pw")
    assert change(client, user, "Password123!", "Password123!").status_code == 400
    assert change(client, user, "Password123!", "short").status_code == 422
    assert change(client, user, "Password123!", "x" * 129).status_code == 422


def test_change_password_requires_auth(client):
    response = client.post("/auth/change-password", json={"current_password": "a", "new_password": "bbbbbb"})
    assert response.status_code == 401


def test_social_account_has_no_password_to_change(client):
    user = social_login(client)
    response = change(client, user, "anything", "NewPassword456!")
    assert response.status_code == 400
    assert "Google" in response.json()["detail"]


def test_also_mounted_under_api_v1(client):
    user = register(client, "pw")
    response = client.post("/api/v1/auth/change-password",
                           json={"current_password": "Password123!", "new_password": "NewPassword456!"},
                           headers=user["headers"])
    assert response.status_code == 200


# ---- Connected apps

def apps_of(client, user):
    response = client.get("/api/v1/users/me/connected-apps", headers=user["headers"])
    assert response.status_code == 200, response.text
    return {app["provider"]: app for app in response.json()["apps"]}


def test_password_account_has_nothing_connected(client):
    apps = apps_of(client, register(client, "apps"))
    assert set(apps) == {"google", "apple"}
    assert not apps["google"]["connected"] and not apps["apple"]["connected"]


def test_social_account_shows_its_provider_as_primary(client):
    apps = apps_of(client, social_login(client, "apple"))
    assert apps["apple"]["connected"] and apps["apple"]["is_primary"] and apps["apple"]["connected_at"]
    assert not apps["google"]["connected"]


def test_google_sign_in_on_a_password_account_is_listed(client):
    user = register(client, "apps")
    social_login(client, "google", email=user["payload"]["email"])
    relogin = login(client, user, "Password123!").json()
    apps = apps_of(client, {"headers": {"Authorization": f"Bearer {relogin['access_token']}"}})
    assert apps["google"]["connected"] and not apps["google"]["is_primary"]


def test_account_created_before_the_table_existed_is_still_connected(client):
    user = social_login(client, "google")
    db_run("DELETE FROM connected_accounts WHERE user_id = $1", user["user_id"])
    apps = apps_of(client, user)
    assert apps["google"]["connected"] and apps["google"]["connected_at"] is None


def test_connected_apps_requires_auth(client):
    assert client.get("/users/me/connected-apps").status_code == 401


def test_profile_tells_the_app_how_the_runner_signs_in(client):
    user = social_login(client, "google")
    assert client.get("/users/me", headers=user["headers"]).json()["auth_provider"] == "google"
    assert client.get(f"/users/{user['user_id']}/profile").json()["auth_provider"] is None


# ---- Delete account

def delete(client, user, **body):
    return client.post("/api/v1/users/me/delete", json=body, headers=user["headers"])


def test_wrong_or_missing_password_deletes_nothing(client):
    user = register(client, "bye")
    assert delete(client, user, password="wrong-password").status_code == 400
    assert delete(client, user).status_code == 400
    assert delete(client, user, confirmation="DELETE").status_code == 400
    assert client.get("/users/me", headers=user["headers"]).status_code == 200


def test_delete_requires_auth(client):
    assert client.post("/users/me/delete", json={"password": "x"}).status_code == 401


def test_delete_removes_the_runner_and_every_personal_record(client):
    user = register(client, "bye")
    friend = register(client, "pal")
    client.post("/api/v1/friends/requests", json={"to_user_id": friend["user_id"]}, headers=user["headers"])
    client.post("/api/v1/friends/requests", json={"to_user_id": user["user_id"]}, headers=friend["headers"])
    claim_run(client, user, square_loop())
    client.post("/users/me/avatar", files={"file": ("a.jpg", tiny_jpeg(), "image/jpeg")}, headers=user["headers"])
    uid = user["user_id"]
    assert db_run("SELECT count(*) FROM telemetry_points p JOIN runs r ON r.id = p.run_id WHERE r.user_id = $1",
                  uid, fetch="fetchval") > 0

    response = delete(client, user, password="Password123!")
    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Account deleted"

    assert client.get("/users/me", headers=user["headers"]).status_code == 401
    assert login(client, user, "Password123!").status_code == 401
    assert client.get(f"/users/{uid}/profile").status_code == 404
    assert client.get("/api/v1/friends", headers=friend["headers"]).json()["total"] == 0
    for table in ("sessions", "runs", "avatars", "avatar_uploads", "user_achievements", "connected_accounts"):
        assert db_run(f"SELECT count(*) FROM {table} WHERE user_id = $1", uid, fetch="fetchval") == 0, table
    assert db_run("SELECT count(*) FROM telemetry_points WHERE run_id NOT IN (SELECT id FROM runs)",
                  fetch="fetchval") == 0


def test_username_and_email_can_be_used_again_after_delete(client):
    user = register(client, "bye")
    delete(client, user, password="Password123!")
    assert client.post("/auth/register", json=user["payload"]).status_code == 200


def test_bases_become_unclaimed_and_lose_the_username(client):
    user = register(client, "bye")
    club_id = client.get("/api/v1/clubs").json()["clubs"][0]["id"]
    client.post(f"/api/v1/clubs/{club_id}/join", headers=user["headers"])
    default = claim_run(client, user, square_loop())
    custom = claim_run(client, user, square_loop(), name="Marina Loop")
    assert default["territory_name"].startswith(user["payload"]["username"])

    assert delete(client, user, password="Password123!").status_code == 200

    base = client.get(f"/api/v1/territories/{default['territory_id']}").json()
    assert base["status"] == "unclaimed"
    assert base["owner_id"] is None and base["owner_username"] == "" and base["club_id"] is None
    assert base["name"] == "Unclaimed Base"
    assert base["record_pace_seconds"] is None
    named = client.get(f"/api/v1/territories/{custom['territory_id']}").json()
    assert named["status"] == "unclaimed" and named["name"] == "Marina Loop"
    assert client.get(f"/api/v1/bases/{default['territory_id']}").json()["user_id"] == ""


def test_next_runner_wins_an_unclaimed_base_without_a_rival_badge(client):
    leaver = register(client, "bye")
    points = square_loop()
    base_id = claim_run(client, leaver, points)["territory_id"]
    delete(client, leaver, password="Password123!")

    runner = register(client, "next")
    result = claim_run(client, runner, points)
    assert result["status"] == "takeover"
    assert result["territory_id"] == base_id  # the old base, not a duplicate on top of it
    assert "Hostile Takeover" not in result["badges_earned"]
    assert result["territory_name"] == f"{runner['payload']['username']}'s Base"

    base = client.get(f"/api/v1/territories/{base_id}").json()
    assert base["status"] == "active"
    assert base["owner_id"] == str(runner["user_id"])


def test_capturing_a_rival_still_earns_the_badge_and_retires_their_default_name(client):
    rival = register(client, "rival")
    points = square_loop()
    base_id = claim_run(client, rival, points)["territory_id"]
    db_run("UPDATE territories SET record_pace_seconds = 99999 WHERE id = $1", base_id)

    runner = register(client, "next")
    result = claim_run(client, runner, points)
    assert result["status"] == "takeover"
    assert "Hostile Takeover" in result["badges_earned"]
    assert rival["payload"]["username"] not in result["territory_name"]
    assert rival["payload"]["username"] not in result["message"]


def test_custom_base_name_survives_capture(client):
    rival = register(client, "rival")
    points = square_loop()
    base_id = claim_run(client, rival, points, name="Marina Loop")["territory_id"]
    db_run("UPDATE territories SET record_pace_seconds = 99999 WHERE id = $1", base_id)
    assert claim_run(client, register(client, "next"), points)["territory_name"] == "Marina Loop"


def test_social_account_must_prove_it_controls_the_provider_account(client):
    user = social_login(client)
    assert delete(client, user).status_code == 400
    assert delete(client, user, token="t", email="someone.else@example.com").status_code == 400
    assert client.get("/users/me", headers=user["headers"]).status_code == 200

    assert delete(client, user, token="t", email=user["email"]).status_code == 200
    assert client.get("/users/me", headers=user["headers"]).status_code == 401


def test_delete_trusts_the_signed_token_and_not_the_request_body(client, monkeypatch):
    user = social_login(client)
    monkeypatch.setenv("SOCIAL_LOGIN_ALLOW_UNVERIFIED", "false")

    async def signed_for_someone_else(provider, token):
        return SocialIdentity(provider, "sub", "attacker@example.com", None)

    monkeypatch.setattr(social_auth, "verify_social_token", signed_for_someone_else)
    assert delete(client, user, token="t", email=user["email"]).status_code == 400

    async def signed_for_the_runner(provider, token):
        return SocialIdentity(provider, "sub", user["email"].upper(), None)

    monkeypatch.setattr(social_auth, "verify_social_token", signed_for_the_runner)
    assert delete(client, user, token="t").status_code == 200


def test_rejected_provider_token_is_400_so_the_app_does_not_log_out(client, monkeypatch):
    user = social_login(client)
    monkeypatch.setenv("SOCIAL_LOGIN_ALLOW_UNVERIFIED", "false")

    async def rejected(provider, token):
        raise SocialAuthError("Invalid Google token")

    monkeypatch.setattr(social_auth, "verify_social_token", rejected)
    assert delete(client, user, token="expired").status_code == 400


# ---- Disconnect and reconnect

def linked_account(client):
    """A password account that has also signed in with Google."""
    user = register(client, "apps", email=f"linked_{uuid.uuid4().hex[:8]}@example.com")
    social_login(client, "google", email=user["payload"]["email"])
    user["headers"] = {
        "Authorization": f"Bearer {login(client, user, 'Password123!').json()['access_token']}"
    }
    return user


def disconnect(client, user, provider="google"):
    return client.delete(f"/api/v1/users/me/connected-apps/{provider}", headers=user["headers"])


def test_disconnect_stops_the_provider_signing_in(client):
    user = linked_account(client)
    assert apps_of(client, user)["google"]["can_disconnect"] is True

    response = disconnect(client, user)
    assert response.status_code == 200, response.text
    assert next(a for a in response.json()["apps"] if a["provider"] == "google")["connected"] is False

    blocked = client.post("/auth/social-login", json={
        "provider": "google", "token": "t", "email": user["payload"]["email"], "name": "Sam",
    })
    assert blocked.status_code == 403
    assert "disconnected" in blocked.json()["detail"]
    assert login(client, user, "Password123!").status_code == 200


def test_reconnect_needs_a_token_for_the_same_email(client):
    user = linked_account(client)
    disconnect(client, user)
    url = "/api/v1/users/me/connected-apps/google"
    wrong = client.post(url, json={"token": "t", "email": "other@example.com"}, headers=user["headers"])
    assert wrong.status_code == 400

    right = client.post(url, json={"token": "t", "email": user["payload"]["email"]}, headers=user["headers"])
    assert right.status_code == 200, right.text
    assert next(a for a in right.json()["apps"] if a["provider"] == "google")["connected"] is True
    relink = client.post("/auth/social-login", json={
        "provider": "google", "token": "t", "email": user["payload"]["email"], "name": "Sam",
    })
    assert relink.status_code == 200


def test_connect_can_add_a_provider_never_used_before(client):
    user = register(client, "apps")
    response = client.post("/users/me/connected-apps/apple",
                           json={"token": "t", "email": user["payload"]["email"]}, headers=user["headers"])
    assert response.status_code == 200
    assert apps_of(client, user)["apple"]["connected"] is True


def test_primary_provider_cannot_be_disconnected(client):
    user = social_login(client, "google")
    assert apps_of(client, user)["google"]["can_disconnect"] is False
    assert disconnect(client, user).status_code == 400
    assert apps_of(client, user)["google"]["connected"] is True


def test_disconnect_errors(client):
    user = register(client, "apps")
    assert disconnect(client, user).status_code == 404
    assert disconnect(client, user, "strava").status_code == 404
    assert client.delete("/users/me/connected-apps/google").status_code == 401


def test_deleted_account_can_sign_up_again_with_a_disconnected_provider(client):
    user = linked_account(client)
    disconnect(client, user)
    delete(client, user, password="Password123!")
    fresh = client.post("/auth/social-login", json={
        "provider": "google", "token": "t", "email": user["payload"]["email"], "name": "Sam",
    })
    assert fresh.status_code == 200


# ---- Premium colors

def set_color(client, user, color):
    return client.put("/users/identity", json={"territory_color": color}, headers=user["headers"])


def test_premium_color_and_lookalikes_need_premium(client):
    user = register(client, "color")
    for color in ("#FFD700", "#ffd700", "#FFD701", "#FFDA10"):
        response = set_color(client, user, color)
        assert response.status_code == 403, color
        assert response.json()["code"] == "FORBIDDEN"
    assert client.get("/users/me", headers=user["headers"]).json()["territory_color"] == "#00E5FF"


def test_free_colors_are_unaffected(client):
    user = register(client, "color")
    for color in ("#00E5FF", "#FF5722", "#123456", "#FFFF00"):
        assert set_color(client, user, color).status_code == 200, color


def test_premium_runner_can_use_premium_colors(client):
    user = register(client, "color")
    db_run("UPDATE users SET is_premium = TRUE WHERE id = $1", user["user_id"])
    assert set_color(client, user, "#FFD700").status_code == 200
    assert client.get("/users/me", headers=user["headers"]).json()["is_premium"] is True
    assert client.get(f"/users/{user['user_id']}/profile").json()["is_premium"] is None


def test_runner_already_holding_a_premium_color_keeps_it(client):
    user = register(client, "color")
    db_run("UPDATE users SET territory_color = '#FFD700' WHERE id = $1", user["user_id"])
    response = client.put("/users/identity", json={"territory_color": "#FFD700", "bio": "still gold"},
                          headers=user["headers"])
    assert response.status_code == 200


# ---- Raw pace for unit conversion

def test_every_pace_has_a_number_next_to_the_text(client):
    user = register(client, "pace")
    club_id = client.get("/api/v1/clubs").json()["clubs"][0]["id"]
    client.post(f"/api/v1/clubs/{club_id}/join", headers=user["headers"])
    points = square_loop(size_deg=0.01)
    activity = client.post("/api/v1/runs/start", json={"title": "Loop"}, headers=user["headers"]).json()["activity_id"]
    client.post(f"/api/v1/runs/{activity}/telemetry", json={"activity_id": activity, "points": points},
                headers=user["headers"])
    db_run("UPDATE runs SET start_time = NOW() - INTERVAL '10 minutes' WHERE activity_id = $1", activity)
    live = client.get(f"/api/v1/runs/{activity}/live", headers=user["headers"]).json()
    assert live["pace_seconds"] > 0
    finished = client.post(f"/api/v1/runs/{activity}/finish", json={}, headers=user["headers"]).json()
    assert finished["pace_seconds"] > 0

    def as_text(seconds):
        return f"{seconds // 60}'{seconds % 60:02d}\""

    assert as_text(finished["pace_seconds"]) == finished["pace_formatted"]
    run = client.get(f"/users/{user['user_id']}/runs", headers=user["headers"]).json()["runs"][0]
    assert as_text(run["pace_seconds"]) == run["pace"]
    stats = client.get("/users/me", headers=user["headers"]).json()["stats"]
    assert as_text(stats["avg_pace_seconds"]) == stats["avg_pace"]
    board = client.get(f"/api/v1/clubs/{club_id}/leaderboard?limit=200", headers=user["headers"]).json()["leaderboard"]
    mine = next(e for e in board if e["is_current_user"])
    assert as_text(mine["avg_pace_seconds"]) == mine["avg_pace"]
