"""
Privacy levels. The point of these tests is that a hidden runner can't be found
through any side door, so each surface that exposes a runner is checked.
"""
import pytest

from tests.conftest import db_run, register, square_loop


def set_level(client, user, level):
    response = client.put("/users/identity", json={"privacy_level": level}, headers=user["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["privacy_level"] == level


def befriend(client, a, b):
    client.post("/api/v1/friends/requests", json={"to_user_id": b["user_id"]}, headers=a["headers"])
    client.post("/api/v1/friends/requests", json={"to_user_id": a["user_id"]}, headers=b["headers"])


def setup_runner(client, level, **overrides):
    """A runner with a club, a default-named base, and bio/city to hide."""
    runner = register(client, "hidden", **overrides)
    club_id = client.get("/api/v1/clubs").json()["clubs"][0]["id"]
    client.post(f"/api/v1/clubs/{club_id}/join", headers=runner["headers"])
    client.put("/users/identity", json={"bio": "my bio", "city": "Secret City"}, headers=runner["headers"])
    points = square_loop()
    base_id = client.post("/api/v1/bases/claim", json={"points": points}, headers=runner["headers"]).json()["base_id"]
    set_level(client, runner, level)
    runner.update(club_id=club_id, base_id=base_id, lat=points[0]["latitude"], lng=points[0]["longitude"])
    return runner


def test_default_is_public_and_only_the_runner_sees_their_level(client):
    runner = register(client, "open")
    other = register(client, "other")
    me = client.get("/users/me", headers=runner["headers"]).json()
    assert me["privacy_level"] == "public"
    assert me["is_restricted"] is False
    assert client.get(f"/users/{runner['user_id']}/profile", headers=other["headers"]).json()["privacy_level"] is None


def test_invalid_level_is_rejected(client):
    runner = register(client, "open")
    assert client.put("/users/identity", json={"privacy_level": "secret"}, headers=runner["headers"]).status_code == 422


def test_existing_profile_edits_leave_the_level_alone(client):
    runner = register(client, "open")
    set_level(client, runner, "private")
    client.put("/users/identity", json={"bio": "changed"}, headers=runner["headers"])
    assert client.get("/users/me", headers=runner["headers"]).json()["privacy_level"] == "private"


@pytest.mark.parametrize("level", ["friends", "private"])
def test_stranger_and_anonymous_get_the_minimal_card(client, level):
    runner = setup_runner(client, level)
    stranger = register(client, "stranger")
    for headers in ({}, stranger["headers"]):
        body = client.get(f"/users/{runner['user_id']}/profile", headers=headers).json()
        assert body["is_restricted"] is True
        assert body["username"] == runner["payload"]["username"]
        assert body["first_name"] == runner["payload"]["username"]
        assert body["last_name"] is None and body["bio"] is None and body["city"] == ""
        assert body["club_id"] is None and body["club_name"] is None and body["favorite_base"] is None
        assert body["total_km"] == 0 and body["total_territories"] == 0
        assert body["stats"]["bases_held"] == 0


@pytest.mark.parametrize("level,friend_sees", [("public", True), ("friends", True), ("private", False)])
def test_friends_see_what_their_level_allows(client, level, friend_sees):
    runner = setup_runner(client, level)
    friend = register(client, "friend")
    befriend(client, runner, friend)
    body = client.get(f"/users/{runner['user_id']}/profile", headers=friend["headers"]).json()
    assert body["is_restricted"] is (not friend_sees)
    assert (body["bio"] == "my bio") is friend_sees
    expected = 200 if friend_sees else 403
    assert client.get(f"/users/{runner['user_id']}/territories", headers=friend["headers"]).status_code == expected
    assert client.get(f"/users/{runner['user_id']}/achievements", headers=friend["headers"]).status_code == expected


def test_runner_always_sees_their_own_data(client):
    runner = setup_runner(client, "private")
    uid, headers = runner["user_id"], runner["headers"]
    me = client.get(f"/users/{uid}/profile", headers=headers).json()
    assert me["is_restricted"] is False and me["bio"] == "my bio" and me["total_territories"] == 1
    assert client.get(f"/users/{uid}/territories", headers=headers).status_code == 200
    assert client.get(f"/users/{uid}/achievements", headers=headers).status_code == 200
    assert client.get(f"/api/v1/bases/user/{uid}/stats", headers=headers).status_code == 200
    detail = client.get(f"/api/v1/territories/{runner['base_id']}", headers=headers).json()
    assert detail["is_owner"] is True and detail["owner_username"] == runner["payload"]["username"]


@pytest.mark.parametrize("level", ["friends", "private"])
def test_owner_filtered_routes_refuse_strangers(client, level):
    runner = setup_runner(client, level)
    stranger = register(client, "stranger")
    uid = runner["user_id"]
    for path in (f"/users/{uid}/territories", f"/api/v1/bases/user/{uid}/stats", f"/api/v1/bases?user_id={uid}"):
        assert client.get(path).status_code == 403, path
        assert client.get(path, headers=stranger["headers"]).status_code == 403, path
    assert client.get(f"/users/{uid}/achievements", headers=stranger["headers"]).status_code == 403


def test_public_runner_is_unchanged(client):
    runner = setup_runner(client, "public")
    uid = runner["user_id"]
    assert client.get(f"/users/{uid}/territories").status_code == 200
    assert client.get(f"/api/v1/bases/user/{uid}/stats").status_code == 200
    detail = client.get(f"/api/v1/territories/{runner['base_id']}").json()
    assert detail["owner_username"] == runner["payload"]["username"]
    assert detail["name"] == f"{runner['payload']['username']}'s Base"


def territory_surfaces(client, runner, headers):
    """Every response that describes the runner's base."""
    near = f"lat={runner['lat']}&lng={runner['lng']}&radius_km=2"
    nearby = client.get(f"/api/v1/territories/nearby?{near}", headers=headers).json()["territories"]
    home = client.get(f"/api/v1/map/home?{near}", headers=headers).json()["territories"]
    bases = client.get("/api/v1/bases?limit=200", headers=headers).json()["bases"]
    club = client.get(f"/api/v1/clubs/{runner['club_id']}/territories", headers=headers).json()["bases"]
    return {
        "detail": client.get(f"/api/v1/territories/{runner['base_id']}", headers=headers).json(),
        "base": client.get(f"/api/v1/bases/{runner['base_id']}", headers=headers).json(),
        "nearby": next(t for t in nearby if t["id"] == runner["base_id"]),
        "home": next(t for t in home if t["id"] == runner["base_id"]),
        "bases": next(t for t in bases if t["id"] == runner["base_id"]),
        "club": next(t for t in club if t["id"] == runner["base_id"]),
    }


@pytest.mark.parametrize("level", ["friends", "private"])
def test_base_stays_on_every_map_surface_without_its_owner(client, level):
    runner = setup_runner(client, level)
    client.put("/users/identity", json={"territory_color": "#123456"}, headers=runner["headers"])
    stranger = register(client, "stranger")
    for headers in ({}, stranger["headers"]):
        for surface, body in territory_surfaces(client, runner, headers).items():
            text = str(body)
            assert runner["payload"]["username"] not in text, surface
            assert "#123456" not in text, surface
            assert str(runner["user_id"]) != str(body.get("owner_id")), surface
            assert body.get("user_id", "") == "", surface
        detail = client.get(f"/api/v1/territories/{runner['base_id']}", headers=headers).json()
        assert detail["owner_username"] == "Private runner"
        assert detail["name"] == "Private Base"
        assert detail["owner_avatar"] == ""


def test_friend_sees_the_owner_of_a_friends_only_base(client):
    runner = setup_runner(client, "friends")
    friend = register(client, "friend")
    befriend(client, runner, friend)
    detail = territory_surfaces(client, runner, friend["headers"])["detail"]
    assert detail["owner_username"] == runner["payload"]["username"]


def test_custom_base_name_is_kept_when_the_owner_is_hidden(client):
    runner = setup_runner(client, "private")
    db_run("UPDATE territories SET name = 'Marina Loop' WHERE id = $1", runner["base_id"])
    assert client.get(f"/api/v1/territories/{runner['base_id']}").json()["name"] == "Marina Loop"


def test_spoofed_legacy_viewer_ids_reveal_nothing(client):
    """?user_id= and ?current_user_id= are unverified, so they can't unlock or confirm anything."""
    runner = setup_runner(client, "private")
    uid, base_id = runner["user_id"], runner["base_id"]

    detail = client.get(f"/api/v1/territories/{base_id}?current_user_id={uid}").json()
    assert detail["owner_username"] == "Private runner"
    assert detail["is_owner"] is False

    home = client.get(f"/api/v1/map/home?lat={runner['lat']}&lng={runner['lng']}&radius_km=2&user_id={uid}").json()
    assert home["user_territories_count"] == 0
    assert not next(t for t in home["territories"] if t["id"] == base_id)["is_user_territory"]

    clubs = client.get(f"/api/v1/clubs?user_id={uid}").json()["clubs"]
    assert not any(c["is_joined"] for c in clubs)

    board = client.get(f"/api/v1/clubs/{runner['club_id']}/leaderboard?limit=200&current_user_id={uid}").json()
    assert not any(e["is_current_user"] for e in board["leaderboard"])


def test_challenge_does_not_name_a_hidden_owner(client):
    runner = setup_runner(client, "private")
    db_run("UPDATE territories SET record_pace_seconds = 300 WHERE id = $1", runner["base_id"])
    stranger = register(client, "stranger")
    response = client.post(f"/api/v1/territories/{runner['base_id']}/challenge", json={}, headers=stranger["headers"])
    assert response.status_code == 200
    assert runner["payload"]["username"] not in str(response.json())
    assert response.json()["current_owner"] == "Private runner"


@pytest.mark.parametrize("level", ["friends", "private"])
def test_leaderboard_keeps_the_rank_but_not_the_runner(client, level):
    runner = setup_runner(client, level)
    stranger = register(client, "stranger")
    url = f"/api/v1/clubs/{runner['club_id']}/leaderboard?limit=200"

    board = client.get(url, headers=stranger["headers"]).json()
    assert runner["payload"]["username"] not in str(board)
    assert any(e["username"] == "Private runner" and e["user_id"] == "" for e in board["leaderboard"])

    own = client.get(url, headers=runner["headers"]).json()["leaderboard"]
    assert next(e for e in own if e["is_current_user"])["username"] == runner["payload"]["username"]


def test_friends_level_runner_is_searchable_by_username_only(client):
    runner = setup_runner(client, "friends", first_name="Xenophon")
    stranger = register(client, "stranger")

    def search(user, q):
        return client.get(f"/api/v1/users/search?q={q}", headers=user["headers"]).json()["results"]

    assert search(stranger, "Xenophon") == []
    found = search(stranger, runner["payload"]["username"])
    assert len(found) == 1
    assert found[0]["first_name"] is None and found[0]["last_name"] is None
    assert found[0]["club_name"] is None and found[0]["total_km"] is None and found[0]["bases_held"] is None

    befriend(client, runner, stranger)
    found = search(stranger, "Xenophon")
    assert found[0]["first_name"] == "Xenophon" and found[0]["club_name"] is not None


def test_private_runner_is_not_searchable_except_by_friends(client):
    runner = setup_runner(client, "private")
    stranger = register(client, "stranger")
    url = f"/api/v1/users/search?q={runner['payload']['username']}"
    assert client.get(url, headers=stranger["headers"]).json()["results"] == []

    # Still reachable by exact username, or nobody could ever become their friend
    sent = client.post("/api/v1/friends/requests", json={"username": runner["payload"]["username"]},
                       headers=stranger["headers"])
    assert sent.status_code == 200
    incoming = client.get("/api/v1/friends/requests", headers=runner["headers"]).json()["incoming"]
    client.post(f"/api/v1/friends/requests/{incoming[0]['id']}/accept", headers=runner["headers"])

    results = client.get(url, headers=stranger["headers"]).json()["results"]
    assert len(results) == 1
    assert results[0]["total_km"] is None  # private means private, even from friends


def test_going_back_to_public_restores_everything(client):
    runner = setup_runner(client, "private")
    set_level(client, runner, "public")
    assert client.get(f"/users/{runner['user_id']}/profile").json()["is_restricted"] is False
    assert client.get(f"/api/v1/territories/{runner['base_id']}").json()["owner_username"] == runner["payload"]["username"]
