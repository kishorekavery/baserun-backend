import os
import pytest
from db.config import DatabaseConfig
from db.external_db import ExternalDatabaseManager, check_external_db_health


def test_database_config_defaults():
    config = DatabaseConfig()
    assert config.db_type == "sqlite"
    assert config.database_path == "baserun.db"
    assert config.external_db_type == "postgresql"
    assert config.external_host == "localhost"
    assert config.external_port == 5432
    assert config.external_user == "postgres"
    assert config.is_external_configured() is False


def test_database_config_custom_env(monkeypatch):
    monkeypatch.setenv("EXTERNAL_DB_HOST", "db.example.com")
    monkeypatch.setenv("EXTERNAL_DB_PORT", "6543")
    monkeypatch.setenv("EXTERNAL_DB_NAME", "my_remote_db")
    monkeypatch.setenv("EXTERNAL_DB_USER", "admin")
    monkeypatch.setenv("EXTERNAL_DB_PASSWORD", "secret#pass@word")
    monkeypatch.setenv("EXTERNAL_DB_SSLMODE", "require")

    cfg = DatabaseConfig.from_env()
    assert cfg.external_host == "db.example.com"
    assert cfg.external_port == 6543
    assert cfg.external_name == "my_remote_db"
    assert cfg.external_user == "admin"
    assert cfg.external_password == "secret#pass@word"
    assert cfg.is_external_configured() is True

    url = cfg.get_external_database_url()
    assert "db.example.com:6543/my_remote_db" in url
    assert "sslmode=require" in url
    assert "admin:secret%23pass%40word" in url


def test_database_config_masked_dict(monkeypatch):
    monkeypatch.setenv("EXTERNAL_DB_PASSWORD", "super_secret_value")
    cfg = DatabaseConfig.from_env()
    masked = cfg.to_dict(mask_secrets=True)
    assert masked["external_password"] == "******"

    unmasked = cfg.to_dict(mask_secrets=False)
    assert unmasked["external_password"] == "super_secret_value"


@pytest.mark.asyncio
async def test_external_db_manager_not_configured():
    cfg = DatabaseConfig(external_password="", external_database_url=None)
    mgr = ExternalDatabaseManager(config=cfg)
    assert mgr.is_configured is False

    health = await mgr.check_health()
    assert health["configured"] is False
    assert health["status"] == "not_configured"


def test_health_db_endpoint(client):
    res = client.get("/health/db")
    assert res.status_code == 200
    data = res.json()
    assert "sqlite" in data
    assert "external_db" in data
    assert "config" in data
    assert "external_password" in data["config"]
    # Ensure password is not leaked in plaintext
    assert data["config"]["external_password"] in ["", "******"]
