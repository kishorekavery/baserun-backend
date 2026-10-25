"""
Friends & friend requests (friends_api.md test checklist).
"""
import asyncio

from tests.conftest import db_run, register


def send(client, sender, target_id):
    return client.post("/api/v1/friends/requests", json={"to_user_id": target_id}, headers=sender["headers"])


def friends_of(client, user):
    return client.get("/api/v1/friends", headers=user["headers"]).json()


def requests_of(client, user):
    return client.get("/api/v1/friends/requests", headers=user["headers"]).json()


def test_all_routes_require_auth(client):
    for method, path in [
        ("get", "/api/v1/users/search?q=ab"),
        ("get", "/api/v1/friends"),
        ("get", "/api/v1/friends/requests"),
        ("post", "/api/v1/friends/requests"),
        ("post", "/api/v1/friends/requests/1/accept"),
        ("post", "/api/v1/friends/requests/1/decline"),
        ("delete", "/api/v1/friends/requests/1"),
        ("delete", "/api/v1/friends/1"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path


def test_send_then_accept(client):
    a, b = register(client, "fa"), register(client, "fb")
    res = send(client, a, b["user_id"])
    assert res.status_code == 200
    sent = res.json()
    assert sent["friendship_status"] == "outgoing_pending"
    assert sent["message"] == f"Friend request sent to @{b['payload']['username']}"

    assert friends_of(client, b)["incoming_pending_count"] == 1
    incoming = requests_of(client, b)["incoming"]
    assert incoming[0]["id"] == sent["request_id"]
    assert incoming[0]["direction"] == "incoming"
    assert incoming[0]["user"]["user_id"] == a["user_id"]
    assert incoming[0]["user"]["friendship_status"] == "incoming_pending"
    outgoing = requests_of(client, a)["outgoing"]
    assert outgoing[0]["user"]["user_id"] == b["user_id"]

    accepted = client.post(f"/api/v1/friends/requests/{sent['request_id']}/accept", headers=b["headers"]).json()
    assert accepted["friendship_status"] == "friends"

    for me, other in ((a, b), (b, a)):
        data = friends_of(client, me)
        assert data["total"] == 1
        assert data["incoming_pending_count"] == 0
        friend = data["friends"][0]
        assert friend["user_id"] == other["user_id"]
        assert friend["friendship_status"] == "friends"
        assert friend["friends_since"].endswith("Z")
        assert "email" not in friend
    assert requests_of(client, b) == {"incoming": [], "outgoing": []}


def test_send_then_decline_allows_resend(client):
    a, b = register(client, "da"), register(client, "db")
    request_id = send(client, a, b["user_id"]).json()["request_id"]
    declined = client.post(f"/api/v1/friends/requests/{request_id}/decline", headers=b["headers"])
    assert declined.json()["friendship_status"] == "none"
    assert requests_of(client, a)["outgoing"] == []
    assert requests_of(client, b)["incoming"] == []
    assert friends_of(client, a)["total"] == 0
    assert send(client, a, b["user_id"]).status_code == 200


def test_send_then_withdraw(client):
    a, b = register(client, "wa"), register(client, "wb")
    request_id = send(client, a, b["user_id"]).json()["request_id"]
    assert client.delete(f"/api/v1/friends/requests/{request_id}", headers=b["headers"]).status_code == 403
    res = client.delete(f"/api/v1/friends/requests/{request_id}", headers=a["headers"])
    assert res.status_code == 200
    assert res.json()["friendship_status"] == "none"
    assert requests_of(client, a)["outgoing"] == []
    assert requests_of(client, b)["incoming"] == []


def test_reverse_send_auto_accepts(client):
    a, b = register(client, "ra"), register(client, "rb")
    send(client, a, b["user_id"])
    res = client.post("/api/v1/friends/requests", json={"username": a["payload"]["username"].upper()}, headers=b["headers"])
    assert res.status_code == 200
    assert res.json()["friendship_status"] == "friends"
    assert res.json()["message"] == f"You are now friends with @{a['payload']['username']}"
    pending = db_run(
        "SELECT count(*) FROM friend_requests WHERE status = 'pending' AND $1 IN (from_user_id, to_user_id)",
        a["user_id"], fetch="fetchval",
    )
    assert pending == 0
    assert friends_of(client, a)["total"] == 1


def test_simultaneous_requests_become_friends(client):
    a, b = register(client, "sa"), register(client, "sb")
    from models.datamodel import SendFriendRequest
    from services import friend_service

    async def race():
        return await asyncio.gather(
            friend_service.send_request({"id": a["user_id"]}, SendFriendRequest(to_user_id=b["user_id"])),
            friend_service.send_request({"id": b["user_id"]}, SendFriendRequest(to_user_id=a["user_id"])),
        )

    results = asyncio.run(race())
    assert sorted(r["friendship_status"] for r in results) == ["friends", "outgoing_pending"]
    statuses = db_run(
        """
        SELECT status FROM friend_requests
        WHERE (from_user_id = $1 AND to_user_id = $2) OR (from_user_id = $2 AND to_user_id = $1)
        """,
        a["user_id"], b["user_id"], fetch="fetch",
    )
    assert [r["status"] for r in statuses] == ["accepted"]
    assert friends_of(client, a)["total"] == 1 and friends_of(client, b)["total"] == 1


def test_error_cases(client):
    a, b, c = register(client, "ea"), register(client, "eb"), register(client, "ec")

    res = send(client, a, a["user_id"])
    assert res.status_code == 400 and res.json()["detail"] == "You can't add yourself"
    res = send(client, a, 99999999)
    assert res.status_code == 404 and res.json()["detail"] == "User not found"
    assert client.post("/api/v1/friends/requests", json={}, headers=a["headers"]).status_code == 422

    request_id = send(client, a, b["user_id"]).json()["request_id"]
    res = send(client, a, b["user_id"])
    assert res.status_code == 409 and res.json()["detail"] == "Friend request already sent"

    # Only the recipient may respond
    for actor in (a, c):
        assert client.post(f"/api/v1/friends/requests/{request_id}/accept", headers=actor["headers"]).status_code == 403
        assert client.post(f"/api/v1/friends/requests/{request_id}/decline", headers=actor["headers"]).status_code == 403

    client.post(f"/api/v1/friends/requests/{request_id}/accept", headers=b["headers"])
    res = send(client, b, a["user_id"])
    assert res.status_code == 409 and res.json()["detail"] == "You are already friends"
    for path in (f"{request_id}/accept", f"{request_id}/decline"):
        res = client.post(f"/api/v1/friends/requests/{path}", headers=b["headers"])
        assert res.status_code == 409 and res.json()["detail"] == "Request is no longer pending"
    assert client.delete(f"/api/v1/friends/requests/{request_id}", headers=a["headers"]).status_code == 409

    res = client.post("/api/v1/friends/requests/99999999/accept", headers=b["headers"])
    assert res.status_code == 404 and res.json()["detail"] == "Friend request not found"
    assert res.json()["code"] == "NOT_FOUND"


def test_search(client):
    me = register(client, "Searcher", first_name="Zelda")
    friend, stranger, pending = register(client, "srchfriend"), register(client, "srchstranger"), register(client, "srchpending")
    rid = send(client, friend, me["user_id"]).json()["request_id"]
    client.post(f"/api/v1/friends/requests/{rid}/accept", headers=me["headers"])
    out_id = send(client, me, pending["user_id"]).json()["request_id"]

    assert client.get("/api/v1/users/search?q=s", headers=me["headers"]).status_code == 422

    data = client.get("/api/v1/users/search?q=SRCH", headers=me["headers"]).json()
    by_id = {r["user_id"]: r for r in data["results"]}
    assert by_id[friend["user_id"]]["friendship_status"] == "friends"
    assert by_id[stranger["user_id"]]["friendship_status"] == "none"
    assert by_id[pending["user_id"]]["friendship_status"] == "outgoing_pending"
    assert by_id[pending["user_id"]]["request_id"] == out_id
    assert data["results"][0]["user_id"] == friend["user_id"]  # friends rank first
    assert all("email" not in r for r in data["results"])

    seen_by_pending = client.get("/api/v1/users/search?q=searcher", headers=pending["headers"]).json()
    match = next(r for r in seen_by_pending["results"] if r["user_id"] == me["user_id"])
    assert match["friendship_status"] == "incoming_pending"
    assert match["request_id"] == out_id

    exact = client.get(f"/api/v1/users/search?q={me['payload']['username']}", headers=me["headers"]).json()
    assert exact["results"][0]["user_id"] == me["user_id"]
    assert exact["results"][0]["friendship_status"] == "self"

    by_name = client.get("/api/v1/users/search?q=zeld", headers=friend["headers"]).json()
    assert any(r["user_id"] == me["user_id"] for r in by_name["results"])

    wildcard = client.get("/api/v1/users/search?q=%25%25", headers=me["headers"]).json()
    assert wildcard["results"] == []


def test_unfriend(client):
    a, b = register(client, "ua"), register(client, "ub")
    rid = send(client, a, b["user_id"]).json()["request_id"]
    client.post(f"/api/v1/friends/requests/{rid}/accept", headers=b["headers"])

    res = client.delete(f"/api/v1/friends/{a['user_id']}", headers=b["headers"])
    assert res.status_code == 200 and res.json()["friendship_status"] == "none"
    assert friends_of(client, a)["total"] == 0 and friends_of(client, b)["total"] == 0
    res = client.delete(f"/api/v1/friends/{a['user_id']}", headers=b["headers"])
    assert res.status_code == 404 and res.json()["detail"] == "You are not friends with this runner"
    assert send(client, b, a["user_id"]).status_code == 200


def test_deleting_user_removes_requests_and_friendships(client):
    a, b, c = register(client, "xa"), register(client, "xb"), register(client, "xc")
    rid = send(client, a, b["user_id"]).json()["request_id"]
    client.post(f"/api/v1/friends/requests/{rid}/accept", headers=b["headers"])
    send(client, a, c["user_id"])

    db_run("DELETE FROM users WHERE id = $1", a["user_id"])
    assert friends_of(client, b)["total"] == 0
    assert requests_of(client, c)["incoming"] == []
    remaining = db_run(
        "SELECT count(*) FROM friend_requests WHERE $1 IN (from_user_id, to_user_id)", a["user_id"], fetch="fetchval"
    )
    assert remaining == 0


def test_rate_limit_pending_outgoing(client, monkeypatch):
    from services import friend_service

    monkeypatch.setattr(friend_service, "MAX_PENDING_OUTGOING", 2)
    sender = register(client, "spam")
    targets = [register(client, "tgt") for _ in range(3)]
    assert send(client, sender, targets[0]["user_id"]).status_code == 200
    assert send(client, sender, targets[1]["user_id"]).status_code == 200
    res = send(client, sender, targets[2]["user_id"])
    assert res.status_code == 429
    assert res.json()["code"] == "TOO_MANY_REQUESTS"
