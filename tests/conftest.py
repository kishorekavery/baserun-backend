"""
Tests run against a real, disposable PostgreSQL database:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/baserun_test pytest

The database name must contain "test"; its public schema is dropped and
recreated at the start of the session.
"""
import asyncio
import os
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-pytest-runs-32bytes-minimum"
os.environ["SOCIAL_LOGIN_ALLOW_UNVERIFIED"] = "true"

from main import app  # noqa: E402
import db.database as database  # noqa: E402


def pytest_collection_modifyitems(config, items):
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL is not set (needs a disposable PostgreSQL database)")
    for item in items:
        item.add_marker(skip)


def db_run(query: str, *args, fetch: str = "execute"):
    """Run a query on a short-lived direct connection (independent of the app pool)."""
    async def _run():
        conn = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            return await getattr(conn, fetch)(query, *args)
        finally:
            await conn.close()
    return asyncio.run(_run())


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    if not TEST_DATABASE_URL:
        yield
        return
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" not in db_name:
        pytest.exit(f"Refusing to reset database '{db_name}': its name must contain 'test'")
    db_run("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    database._state.schema_ready = False
    yield


@pytest.fixture(scope="function")
def client():
    with TestClient(app) as test_client:
        yield test_client


def register(client, prefix: str = "runner", **overrides) -> dict:
    """Registers a fresh user and returns {payload, token, user_id, headers}."""
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"{prefix}_{suffix}",
        "email": f"{prefix}_{suffix}@example.com",
        "password": "Password123!",
        "first_name": "Test",
        "last_name": "Runner",
        **overrides,
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    return {
        "payload": payload,
        "token": data["access_token"],
        "user_id": data["user_id"],
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
    }


@pytest.fixture(scope="function")
def registered_user(client):
    user_payload = {
        "username": "fastrunner",
        "password": "SecurePassword123!",
        "first_name": "Speedy",
        "last_name": "Gonzales",
        "email": "speedy@example.com",
        "phone": "+1234567890"
    }
    # Attempt registration (ignore if already registered)
    client.post("/api/v1/bases/register", json=user_payload)
    return user_payload


@pytest.fixture(scope="function")
def auth_headers(client, registered_user):
    res = client.post("/api/v1/bases/login", json={
        "username": registered_user["username"],
        "password": registered_user["password"]
    })
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def square_loop(lat: float = None, lng: float = None, size_deg: float = 0.0015) -> list:
    """
    A closed square loop (~4 x 165 m by default) with increasing timestamps.
    Without lat/lng a random spot is used so tests never overlap each other's bases.
    """
    import random
    from datetime import datetime, timedelta, timezone

    if lat is None:
        lat = 12.5 + random.random()
    if lng is None:
        lng = 79.5 + random.random()
    corners = [(lat, lng), (lat + size_deg, lng), (lat + size_deg, lng + size_deg), (lat, lng + size_deg), (lat, lng)]
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    return [
        {"latitude": la, "longitude": lo, "timestamp": (start + timedelta(seconds=60 * i)).isoformat(),
         "speed": 3.0, "accuracy": 4.0}
        for i, (la, lo) in enumerate(corners)
    ]
