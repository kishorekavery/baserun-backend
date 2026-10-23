import uuid
from models.datamodel import Message


def test_root_endpoint(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "message" in res.json()


def test_register_success(client):
    unique_username = f"user_{uuid.uuid4().hex[:8]}"
    payload = {
        "username": unique_username,
        "password": "Password123!",
        "first_name": "John",
        "last_name": "Doe",
        "email": f"{unique_username}@example.com",
        "phone": "+9876543210"
    }
    res = client.post("/api/v1/bases/register", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["message"] == Message.USER_CREATED


def test_register_duplicate_username(client):
    unique_username = f"dup_{uuid.uuid4().hex[:8]}"
    payload = {
        "username": unique_username,
        "password": "Password123!",
        "first_name": "Jane",
        "last_name": "Doe",
        "email": f"{unique_username}_1@example.com",
        "phone": "+9876543211"
    }
    res1 = client.post("/api/v1/bases/register", json=payload)
    assert res1.status_code == 200

    # Try registering again with the exact same username
    payload2 = payload.copy()
    payload2["email"] = f"{unique_username}_2@example.com"
    res2 = client.post("/api/v1/bases/register", json=payload2)
    assert res2.status_code == 400
    assert Message.ALREADY_EXISTS in res2.json()["detail"]


def test_register_duplicate_email(client):
    unique_email = f"dup_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "username": f"user1_{uuid.uuid4().hex[:6]}",
        "password": "Password123!",
        "first_name": "Alice",
        "last_name": "Smith",
        "email": unique_email,
        "phone": "+1122334455"
    }
    res1 = client.post("/api/v1/bases/register", json=payload)
    assert res1.status_code == 200

    # Register different username but same email
    payload2 = {
        "username": f"user2_{uuid.uuid4().hex[:6]}",
        "password": "Password123!",
        "first_name": "Bob",
        "last_name": "Smith",
        "email": unique_email,
        "phone": "+1122334466"
    }
    res2 = client.post("/api/v1/bases/register", json=payload2)
    assert res2.status_code == 400
    assert Message.ALREADY_EXISTS in res2.json()["detail"]


def test_login_with_username(client, registered_user):
    res = client.post("/api/v1/bases/login", json={
        "username": registered_user["username"],
        "password": registered_user["password"]
    })
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["message"] == Message.LOGIN_SUCCESSFUL
    assert data["expires_in"] > 0


def test_login_with_email(client, registered_user):
    res = client.post("/api/v1/bases/login", json={
        "email": registered_user["email"],
        "password": registered_user["password"]
    })
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["message"] == Message.LOGIN_SUCCESSFUL


def test_login_invalid_password(client, registered_user):
    res = client.post("/api/v1/bases/login", json={
        "username": registered_user["username"],
        "password": "WrongPassword999!"
    })
    assert res.status_code == 401
    assert Message.INVALID_USERNAME_OR_PASSWORD in res.json()["detail"]


def test_login_non_existent_user(client):
    res = client.post("/api/v1/bases/login", json={
        "username": "user_does_not_exist_9999",
        "password": "Password123!"
    })
    # Same response as a wrong password, so usernames cannot be enumerated
    assert res.status_code == 401
    assert Message.INVALID_USERNAME_OR_PASSWORD in res.json()["detail"]


def test_get_current_user_me(client, auth_headers, registered_user):
    res = client.get("/api/v1/bases/me", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == registered_user["username"]
    assert data["email"] == registered_user["email"]
    assert "id" in data


def test_get_me_unauthorized(client):
    res = client.get("/api/v1/bases/me")
    assert res.status_code == 401


def test_get_me_invalid_token(client):
    res = client.get("/api/v1/bases/me", headers={"Authorization": "Bearer invalid.token.value"})
    assert res.status_code == 401
