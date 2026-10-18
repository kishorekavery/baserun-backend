import asyncio
import os
import pytest
from fastapi.testclient import TestClient

# Ensure test database is used
TEST_DB_PATH = "test_baserun.db"
os.environ["DATABASE_PATH"] = TEST_DB_PATH
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-pytest-runs-32bytes-minimum"


import db.db_conn as db_module
db_module.DATABASE_PATH = TEST_DB_PATH

from main import app
from db.db_conn import init_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    # Remove old test DB if present
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    # Initialize tables
    asyncio.run(init_db())
    yield
    # Cleanup test DB after all tests finish
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


@pytest.fixture(scope="function")
def client():
    return TestClient(app)


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
