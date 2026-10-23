import asyncio

import asyncpg
import pytest

from db.config import DatabaseConfig
from db.database import apply_schema
from tests.conftest import TEST_DATABASE_URL


def test_database_config_defaults():
    config = DatabaseConfig()
    assert config.host == "localhost"
    assert config.port == 5432
    assert config.user == "postgres"
    assert config.database_url is None
    assert config.get_database_url() == "postgresql://postgres@localhost:5432/baserun_db?sslmode=prefer"


def test_database_config_custom_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("EXTERNAL_DATABASE_URL", raising=False)
    monkeypatch.setenv("EXTERNAL_DB_HOST", "db.example.com")
    monkeypatch.setenv("EXTERNAL_DB_PORT", "6543")
    monkeypatch.setenv("EXTERNAL_DB_NAME", "my_remote_db")
    monkeypatch.setenv("EXTERNAL_DB_USER", "admin")
    monkeypatch.setenv("EXTERNAL_DB_PASSWORD", "secret#pass@word")
    monkeypatch.setenv("EXTERNAL_DB_SSLMODE", "require")

    cfg = DatabaseConfig.from_env()
    assert cfg.host == "db.example.com"
    assert cfg.port == 6543
    url = cfg.get_database_url()
    assert "db.example.com:6543/my_remote_db" in url
    assert "sslmode=require" in url
    assert "admin:secret%23pass%40word" in url


def test_database_url_takes_precedence(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("EXTERNAL_DB_HOST", "ignored.example.com")
    assert DatabaseConfig.from_env().get_database_url() == "postgresql://u:p@h:5432/d"


def test_database_config_masked_dict(monkeypatch):
    monkeypatch.setenv("EXTERNAL_DB_PASSWORD", "super_secret_value")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:super_secret_value@host:5432/db")
    cfg = DatabaseConfig.from_env()
    masked = cfg.to_dict(mask_secrets=True)
    assert masked["password"] == "******"
    assert "super_secret_value" not in masked["database_url"]
    assert cfg.to_dict(mask_secrets=False)["password"] == "super_secret_value"


def test_health_db_endpoint(client):
    res = client.get("/health/db")
    assert res.status_code == 200
    data = res.json()
    assert data["database"] == "Ready"
    assert data["connection"] == "ok"
    assert isinstance(data["active_sessions"], int)
    assert data["config"]["db_type"] == "postgresql"
    assert data["config"]["password"] in ["", "******"]


def test_schema_is_idempotent(client):
    async def run():
        conn = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await apply_schema(conn)
            await apply_schema(conn)
            return await conn.fetchval("SELECT count(*) FROM achievements")
        finally:
            await conn.close()
    assert asyncio.run(run()) >= 6


def test_legacy_tables_are_renamed_not_dropped():
    """A database created by the old code base is preserved as legacy_* tables."""
    base = TEST_DATABASE_URL.rsplit("/", 1)[0]
    scratch_db = "baserun_test_legacy"

    async def run():
        admin = await asyncpg.connect(f"{base}/postgres")
        try:
            await admin.execute(f"DROP DATABASE IF EXISTS {scratch_db}")
            await admin.execute(f"CREATE DATABASE {scratch_db}")
        finally:
            await admin.close()
        conn = await asyncpg.connect(f"{base}/{scratch_db}")
        try:
            await conn.execute("""
                CREATE TABLE users (id SERIAL PRIMARY KEY, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE);
                CREATE INDEX idx_users_club ON users(id);
                CREATE TABLE runs (id SERIAL PRIMARY KEY, activity_id TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL);
                CREATE INDEX idx_runs_user ON runs(user_id);
                CREATE TABLE claimed_bases (id SERIAL PRIMARY KEY, user_id TEXT NOT NULL);
                INSERT INTO users (username, email) VALUES ('old', 'old@example.com');
            """)
            await apply_schema(conn)
            legacy_user = await conn.fetchval("SELECT username FROM legacy_users")
            new_users = await conn.fetchval("SELECT count(*) FROM users")
            has_territories = await conn.fetchval("SELECT to_regclass('public.territories') IS NOT NULL")
            # A second start must not rename the new tables again
            await apply_schema(conn)
            still_new = await conn.fetchval("SELECT to_regclass('public.users') IS NOT NULL")
            return legacy_user, new_users, has_territories, still_new
        finally:
            await conn.close()
            admin = await asyncpg.connect(f"{base}/postgres")
            try:
                await admin.execute(f"DROP DATABASE IF EXISTS {scratch_db}")
            finally:
                await admin.close()

    try:
        legacy_user, new_users, has_territories, still_new = asyncio.run(run())
    except asyncpg.InsufficientPrivilegeError:
        pytest.skip("test role cannot create databases")
    assert legacy_user == "old"
    assert new_users == 0
    assert has_territories
    assert still_new
