"""
Smoke-tests every endpoint and writes endpoint_test_results.json.
Writes test users/runs, so it only runs against a disposable database:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/baserun_test python scripts/test_all_endpoints.py
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not os.getenv("TEST_DATABASE_URL"):
    sys.exit("Set TEST_DATABASE_URL to a disposable PostgreSQL database to run this script.")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.setdefault("SOCIAL_LOGIN_ALLOW_UNVERIFIED", "true")

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
client.__enter__()
results = []

def record(category, function_name, method, endpoint, status_code, ok, note=""):
    results.append({
        "category": category,
        "function": function_name,
        "method": method,
        "endpoint": endpoint,
        "status_code": status_code,
        "status": "PASS" if ok else "FAIL",
        "note": note
    })
    print(f"[{'PASS' if ok else 'FAIL'}] {method} {endpoint} -> {status_code} ({note})")

# ==========================================
# 6. App Config & Health Check
# ==========================================
cat = "6. App Config & Health Check"
r = client.get("/")
record(cat, "getRootHealth()", "GET", "/", r.status_code, r.status_code == 200, r.json().get("message", ""))

r = client.get("/health/db")
record(cat, "getDbHealth()", "GET", "/health/db", r.status_code, r.status_code == 200, "DB Status: Ready")

r = client.get("/api/v1/app/config")
record(cat, "getAppConfig()", "GET", "/api/v1/app/config", r.status_code, r.status_code == 200, r.json().get("app_name", ""))

r = client.get("/api/v1/app/color-palette")
record(cat, "getColorPalette()", "GET", "/api/v1/app/color-palette", r.status_code, r.status_code == 200, f"{len(r.json().get('colors', []))} colors")

# ==========================================
# 1. Authentication & Identity
# ==========================================
cat = "1. Authentication & Identity"
unique_suffix = uuid.uuid4().hex[:6]
username = f"runner_{unique_suffix}"
email = f"runner_{unique_suffix}@example.com"
password = "Password123!"

# checkUsername()
r = client.get(f"/auth/check-username?username={username}")
record(cat, "checkUsername()", "GET", f"/auth/check-username?username={username}", r.status_code, r.status_code == 200, f"available: {r.json().get('available')}")

# register() via /auth/register
reg_payload = {
    "username": username,
    "email": email,
    "password": password,
    "first_name": "Test",
    "last_name": "Runner"
}
r = client.post("/auth/register", json=reg_payload)
record(cat, "register()", "POST", "/auth/register", r.status_code, r.status_code == 200, "Registered via /auth/register")
user_id = r.json().get("user_id")

# register() via alias /api/v1/bases/register
alt_username = f"alt_{unique_suffix}"
r = client.post("/api/v1/bases/register", json={
    "username": alt_username,
    "email": f"{alt_username}@example.com",
    "password": password
})
record(cat, "register() [alias]", "POST", "/api/v1/bases/register", r.status_code, r.status_code == 200, "Registered via /api/v1/bases/register")

# login() via /auth/login
r = client.post("/auth/login", json={"username": username, "password": password})
token = r.json().get("access_token")
record(cat, "login()", "POST", "/auth/login", r.status_code, r.status_code == 200, "JWT token returned")
auth_headers = {"Authorization": f"Bearer {token}"}

# login() via alias /api/v1/bases/login
r = client.post("/api/v1/bases/login", json={"username": username, "password": password})
token = r.json().get("access_token")
auth_headers = {"Authorization": f"Bearer {token}"}
record(cat, "login() [alias]", "POST", "/api/v1/bases/login", r.status_code, r.status_code == 200, "Alias login working")

# socialLogin()
social_payload = {
    "provider": "google",
    "token": f"oauth_google_{unique_suffix}",
    "email": f"google_{unique_suffix}@gmail.com",
    "name": "Google Runner"
}
r = client.post("/auth/social-login", json=social_payload)
record(cat, "socialLogin()", "POST", "/auth/social-login", r.status_code, r.status_code == 200, "Social login authenticated")

# ==========================================
# 4. User Profile & Runner History
# ==========================================
cat = "4. User Profile & Runner History"
# getCurrentUserProfile()
r = client.get("/users/me", headers=auth_headers)
record(cat, "getCurrentUserProfile()", "GET", "/users/me", r.status_code, r.status_code == 200, f"Profile user: {r.json().get('username')}")
numeric_user_id = r.json().get("id")

# updateIdentity()
r = client.put("/users/identity", json={
    "territory_color": "#00E5FF",
    "avatar_url": "https://example.com/avatar.png"
}, headers=auth_headers)
record(cat, "updateIdentity()", "PUT", "/users/identity", r.status_code, r.status_code == 200, f"Color updated: {r.json().get('territory_color')}")

# getMyFullProfile()
r = client.get("/users/profile", headers=auth_headers)
record(cat, "getMyFullProfile()", "GET", "/users/profile", r.status_code, r.status_code == 200, f"Total km: {r.json().get('total_km')}")

# getUserProfileById()
r = client.get(f"/users/{numeric_user_id}/profile")
record(cat, "getUserProfileById()", "GET", f"/users/{numeric_user_id}/profile", r.status_code, r.status_code == 200, "Public profile fetched")

# getUserTerritories()
r = client.get(f"/users/{numeric_user_id}/territories")
record(cat, "getUserTerritories()", "GET", f"/users/{numeric_user_id}/territories", r.status_code, r.status_code == 200, f"Bases: {len(r.json().get('bases', []))}")

# getUserAchievements()
r = client.get(f"/users/{numeric_user_id}/achievements", headers=auth_headers)
record(cat, "getUserAchievements()", "GET", f"/users/{numeric_user_id}/achievements", r.status_code, r.status_code == 200, f"Badges: {r.json().get('total_badges')}")

# getUserStats()
r = client.get(f"/api/v1/bases/user/{numeric_user_id}/stats")
record(cat, "getUserStats()", "GET", f"/api/v1/bases/user/{numeric_user_id}/stats", r.status_code, r.status_code == 200, f"Total bases: {r.json().get('total_bases')}")

# ==========================================
# 5. Clubs & Social
# ==========================================
cat = "5. Clubs & Social"
# listClubs()
r = client.get(f"/api/v1/clubs?user_id={numeric_user_id}")
record(cat, "listClubs()", "GET", f"/api/v1/clubs?user_id={numeric_user_id}", r.status_code, r.status_code == 200, f"Clubs: {len(r.json().get('clubs', []))}")

# getClubDetails()
r = client.get("/api/v1/clubs/1")
record(cat, "getClubDetails()", "GET", "/api/v1/clubs/1", r.status_code, r.status_code == 200, r.json().get("name", ""))

# joinClub()
r = client.post("/api/v1/clubs/1/join", headers=auth_headers)
record(cat, "joinClub()", "POST", "/api/v1/clubs/1/join", r.status_code, r.status_code == 200, r.json().get("message", ""))

# getClubLeaderboard()
r = client.get(f"/api/v1/clubs/1/leaderboard?current_user_id={numeric_user_id}")
record(cat, "getClubLeaderboard()", "GET", f"/api/v1/clubs/1/leaderboard?current_user_id={numeric_user_id}", r.status_code, r.status_code == 200, f"Leaderboard entries: {len(r.json().get('leaderboard', []))}")

# getClubTerritories()
r = client.get("/api/v1/clubs/1/territories")
record(cat, "getClubTerritories()", "GET", "/api/v1/clubs/1/territories", r.status_code, r.status_code == 200, f"Club territories: {r.json().get('total')}")

# ==========================================
# 3. Territories & Home Map
# ==========================================
cat = "3. Territories & Home Map"
# getNearbyTerritories()
r = client.get("/api/v1/territories/nearby?lat=13.0827&lng=80.2707&radius_km=5.0")
record(cat, "getNearbyTerritories()", "GET", "/api/v1/territories/nearby", r.status_code, r.status_code == 200, f"Nearby: {r.json().get('total_nearby')}")

# getHomeMap()
r = client.get(f"/api/v1/map/home?user_id={numeric_user_id}&lat=13.0827&lng=80.2707")
record(cat, "getHomeMap()", "GET", f"/api/v1/map/home?user_id={numeric_user_id}", r.status_code, r.status_code == 200, f"Territories on map: {len(r.json().get('territories', []))}")

# getBases()
r = client.get(f"/api/v1/bases?user_id={numeric_user_id}&limit=10&offset=0")
record(cat, "getBases()", "GET", f"/api/v1/bases?user_id={numeric_user_id}", r.status_code, r.status_code == 200, f"Total bases: {r.json().get('total')}")

# claimBase() via /api/v1/bases/claim (legacy point circuit)
claim_base_payload = {
    "points": [
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0830, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0830, "longitude": 80.2710, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0820, "longitude": 80.2710, "timestamp": datetime.now(timezone.utc).isoformat()},
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": datetime.now(timezone.utc).isoformat()}
    ]
}
r = client.post("/api/v1/bases/claim", json=claim_base_payload, headers=auth_headers)
claimed_base_id = r.json().get("base_id", 1)
record(cat, "claimBase()", "POST", "/api/v1/bases/claim", r.status_code, r.status_code == 200, f"Claimed Base ID: {claimed_base_id}")

# getBaseById()
r = client.get(f"/api/v1/bases/{claimed_base_id}")
record(cat, "getBaseById()", "GET", f"/api/v1/bases/{claimed_base_id}", r.status_code, r.status_code == 200, f"Base ID {claimed_base_id} status: {r.json().get('status')}")

# getTerritoryDetails()
r = client.get(f"/api/v1/territories/1?current_user_id={numeric_user_id}")
record(cat, "getTerritoryDetails()", "GET", "/api/v1/territories/1", r.status_code, r.status_code == 200, f"Territory: {r.json().get('name')}")

# challengeTerritory()
r = client.post("/api/v1/territories/1/challenge", json={}, headers=auth_headers)
record(cat, "challengeTerritory()", "POST", "/api/v1/territories/1/challenge", r.status_code, r.status_code == 200, f"Challenge ID: {r.json().get('challenge_id')}")

# ==========================================
# 2. Run Session & Telemetry Tracking
# ==========================================
cat = "2. Run Session & Telemetry Tracking"
# startRun()
r = client.post("/api/v1/runs/start", json={"title": "Connectivity Test Run"}, headers=auth_headers)
record(cat, "startRun()", "POST", "/api/v1/runs/start", r.status_code, r.status_code == 200, f"Activity ID: {r.json().get('activity_id')}")
act_id = r.json().get("activity_id")

# postTelemetry()
now_iso = datetime.now(timezone.utc).isoformat()
telemetry_payload = {
    "points": [
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": now_iso, "speed": 3.0, "accuracy": 3.0},
        {"latitude": 13.0830, "longitude": 80.2700, "timestamp": now_iso, "speed": 3.2, "accuracy": 3.0},
        {"latitude": 13.0830, "longitude": 80.2710, "timestamp": now_iso, "speed": 3.1, "accuracy": 3.0},
        {"latitude": 13.0820, "longitude": 80.2710, "timestamp": now_iso, "speed": 3.0, "accuracy": 3.0},
        {"latitude": 13.0820, "longitude": 80.2700, "timestamp": now_iso, "speed": 2.9, "accuracy": 3.0}
    ]
}
r = client.post(f"/api/v1/runs/{act_id}/telemetry", json=telemetry_payload, headers=auth_headers)
record(cat, "postTelemetry()", "POST", f"/api/v1/runs/{act_id}/telemetry", r.status_code, r.status_code == 200, f"Saved points: {r.json().get('saved_points')}")

# getLiveRunStats()
r = client.get(f"/api/v1/runs/{act_id}/live", headers=auth_headers)
record(cat, "getLiveRunStats()", "GET", f"/api/v1/runs/{act_id}/live", r.status_code, r.status_code == 200, f"Distance: {r.json().get('distance_km')} km, Pace: {r.json().get('pace_formatted')}")

# getRunPath()
r = client.get(f"/api/v1/runs/{act_id}/path", headers=auth_headers)
record(cat, "getRunPath()", "GET", f"/api/v1/runs/{act_id}/path", r.status_code, r.status_code == 200, f"Breadcrumbs: {r.json().get('total_points')}")

# getActivityTelemetry()
r = client.get(f"/api/v1/bases/telemetry/{act_id}", headers=auth_headers)
record(cat, "getActivityTelemetry()", "GET", f"/api/v1/bases/telemetry/{act_id}", r.status_code, r.status_code == 200, f"Telemetry pts: {r.json().get('total_points')}")

# pauseRun()
r = client.post(f"/api/v1/runs/{act_id}/pause", headers=auth_headers)
record(cat, "pauseRun()", "POST", f"/api/v1/runs/{act_id}/pause", r.status_code, r.status_code == 200, f"Status: {r.json().get('status')}")

# resumeRun()
r = client.post(f"/api/v1/runs/{act_id}/resume", headers=auth_headers)
record(cat, "resumeRun()", "POST", f"/api/v1/runs/{act_id}/resume", r.status_code, r.status_code == 200, f"Status: {r.json().get('status')}")

# finishRun()
r = client.post(f"/api/v1/runs/{act_id}/finish", json={"steps": 3200, "calories": 210, "elevation_gain_m": 5.0}, headers=auth_headers)
record(cat, "finishRun()", "POST", f"/api/v1/runs/{act_id}/finish", r.status_code, r.status_code == 200, f"Finished, can_claim: {r.json().get('can_claim_territory')}")

# getRunSummary()
r = client.get(f"/api/v1/runs/{act_id}/summary", headers=auth_headers)
record(cat, "getRunSummary()", "GET", f"/api/v1/runs/{act_id}/summary", r.status_code, r.status_code == 200, f"Distance: {r.json().get('distance_km')} km")

# getUserRuns()
r = client.get(f"/users/{numeric_user_id}/runs", headers=auth_headers)
record(cat, "getUserRuns()", "GET", f"/users/{numeric_user_id}/runs", r.status_code, r.status_code == 200, f"User runs count: {r.json().get('total_runs')}")

# claimTerritoryFromRun()
r = client.post("/api/v1/territories/claim-from-run", json={
    "activity_id": act_id,
    "territory_name": f"Turf #{unique_suffix}"
}, headers=auth_headers)
record(cat, "claimTerritoryFromRun()", "POST", "/api/v1/territories/claim-from-run", r.status_code, r.status_code == 200, f"Claimed territory ID: {r.json().get('territory_id')}")

# ==========================================
# Logout
# ==========================================
r = client.post("/auth/logout", headers=auth_headers)
record("1. Authentication & Identity", "logout()", "POST", "/auth/logout", r.status_code, r.status_code == 200, "Token invalidated on server")

# Summary
total = len(results)
passed = sum(1 for r in results if r["status"] == "PASS")
failed = total - passed
print("\n" + "="*50)
print(f"TOTAL ENDPOINTS TESTED: {total}")
print(f"PASSED: {passed}")
print(f"FAILED: {failed}")
print("="*50)

with open("endpoint_test_results.json", "w") as f:
    json.dump(results, f, indent=2)
