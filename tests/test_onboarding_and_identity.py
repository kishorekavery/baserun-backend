import uuid

from tests.conftest import square_loop


def test_screen_1_splash_config(client):
    """Screen 1: Splash Screen API"""
    res = client.get("/api/v1/app/config")
    assert res.status_code == 200
    data = res.json()
    assert data["app_name"] == "BASER"
    assert data["tagline"] == "Turn Your Miles into Territory"
    assert data["current_season"] == "Season 1: Chennai Conquest"
    assert data["active_season"] == data["current_season"]
    assert data["maintenance_mode"] is False
    assert data["min_version"] == "1.0.0"
    assert data["server_time"].endswith("Z")


def test_screen_2_social_login_google(client):
    """Screen 2: Login - Continue with Google"""
    payload = {
        "provider": "google",
        "token": "oauth2_google_fake_token_123",
        "email": f"google_{uuid.uuid4().hex[:6]}@gmail.com",
        "name": "Alex Runner"
    }
    res = client.post("/api/v1/auth/social-login", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_screen_2_social_login_apple(client):
    """Screen 2: Login - Continue with Apple"""
    payload = {
        "provider": "apple",
        "token": "apple_id_token_abc_456",
        "email": f"apple_{uuid.uuid4().hex[:6]}@apple.com",
        "name": "Sam Swift"
    }
    res = client.post("/api/v1/auth/social-login", json=payload)
    assert res.status_code == 200
    assert "access_token" in res.json()


def test_screen_3_check_username_available(client):
    """Screen 3: Register - Real-time username check available"""
    unique_user = f"run_{uuid.uuid4().hex[:8]}"
    res = client.get(f"/api/v1/auth/check-username?username={unique_user}")
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert "available" in data["message"].lower()


def test_screen_3_check_username_taken(client, registered_user):
    """Screen 3: Register - Real-time username check taken"""
    res = client.get(f"/api/v1/auth/check-username?username={registered_user['username']}")
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert "already taken" in data["message"].lower()


def test_screen_4_color_palette(client):
    """Screen 4: Choose Your Identity - Color swatches palette"""
    res = client.get("/api/v1/app/color-palette")
    assert res.status_code == 200
    data = res.json()
    hex_list = [c["hex"] for c in data["colors"]]
    assert "#00E5FF" in hex_list  # Electric Cyan
    assert "#FF007F" in hex_list  # Hot Magenta
    assert any(c["is_premium"] for c in data["colors"])


def test_screen_4_update_identity(client, auth_headers):
    """Screen 4: Choose Your Identity - Pick unique color & avatar"""
    payload = {
        "territory_color": "#00E5FF",
        "avatar_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb"
    }
    res = client.put("/api/v1/users/identity", json=payload, headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["territory_color"] == "#00E5FF"
    assert "Identity updated" in data["message"]


def test_screen_5_location_permission_nearby(client, auth_headers):
    """Screen 5: Location Permission - Preview nearby territories"""
    loop = square_loop()
    client.post("/api/v1/bases/claim", json={"points": loop}, headers=auth_headers)
    lat, lng = loop[0]["latitude"], loop[0]["longitude"]
    res = client.get(f"/api/v1/territories/nearby?lat={lat}&lng={lng}&radius_km=5.0")
    assert res.status_code == 200
    data = res.json()
    assert data["total_nearby"] >= 1
    assert len(data["territories"]) >= 1
    t = data["territories"][0]
    assert "name" in t
    assert "area_km2" in t


def test_flutter_api_config_register_and_login_flow(client):
    """
    Direct verification of Flutter ApiConfig endpoints:
    - POST /auth/register
    - POST /auth/login
    - GET /users/me
    """
    unique_user = f"flutter_{uuid.uuid4().hex[:6]}"
    email = f"{unique_user}@flutter.dev"
    password = "FlutterPassword123"

    # 1. Register via /auth/register (as configured in ApiConfig.registerPath)
    reg_res = client.post("/auth/register", json={
        "username": unique_user,
        "email": email,
        "password": password,
        "confirm_password": password
    })
    assert reg_res.status_code == 200
    reg_data = reg_res.json()
    assert "message" in reg_data

    # 2. Login via /auth/login (as configured in ApiConfig.loginPath)
    login_res = client.post("/auth/login", json={
        "username": unique_user,
        "password": password
    })
    assert login_res.status_code == 200
    login_data = login_res.json()
    token = login_data["access_token"]
    assert token is not None
    assert login_data.get("token") == token

    # 3. Fetch profile via /users/me (as configured in ApiConfig.profilePath)
    me_res = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["username"] == unique_user
    assert me_data["email"] == email
    assert "total_territories" in me_data

