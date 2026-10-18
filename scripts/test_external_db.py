import asyncio
import os
import sys
from pathlib import Path

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# Force load latest .env
load_dotenv(override=True)

from db.config import DatabaseConfig
from db.external_db import ExternalDatabaseManager


async def run_tests():
    print("=" * 60)
    print("TESTING EXTERNAL DATABASE CONNECTION (DOCKER POSTGRES)")
    print("=" * 60)

    cfg = DatabaseConfig.from_env()
    print(f"Host: {cfg.external_host}:{cfg.external_port}")
    print(f"Database: {cfg.external_name}")
    print(f"User: {cfg.external_user}")
    print(f"Configured: {cfg.is_external_configured()}")
    masked = cfg.to_dict(mask_secrets=True)
    print(f"Masked Config: {masked}")

    mgr = ExternalDatabaseManager(config=cfg)

    # 1. Health check (ping)
    print("\n[Step 1] Running health check ping...")
    health = await mgr.check_health()
    print(f"Health check status: {health.get('status')}")
    print(f"Ping latency: {health.get('latency_ms')} ms")
    assert health.get("status") == "connected", f"Health check failed: {health}"

    # 2. Initialize connection pool
    print("\n[Step 2] Initializing asyncpg connection pool...")
    pool_ok = await mgr.init_pool()
    assert pool_ok is True, "Failed to initialize pool"
    print("Pool initialized successfully.")

    # 3. Create table
    print("\n[Step 3] Executing DDL (CREATE TABLE)...")
    await mgr.execute("""
        CREATE TABLE IF NOT EXISTS test_external_runners (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            score INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("Table 'test_external_runners' created or verified.")

    # 4. Insert data
    print("\n[Step 4] Inserting test record...")
    await mgr.execute("DELETE FROM test_external_runners WHERE username = $1", "docker_runner_1")
    await mgr.execute(
        "INSERT INTO test_external_runners (username, score) VALUES ($1, $2)",
        "docker_runner_1",
        250,
    )
    print("Record inserted.")

    # 5. Fetch single row
    print("\n[Step 5] Fetching single row (fetch_one)...")
    row = await mgr.fetch_one(
        "SELECT * FROM test_external_runners WHERE username = $1", "docker_runner_1"
    )
    print(f"Fetched row: {row}")
    assert row is not None
    assert row["username"] == "docker_runner_1"
    assert row["score"] == 250

    # 6. Fetch multiple rows
    print("\n[Step 6] Fetching all rows (fetch_all)...")
    rows = await mgr.fetch_all("SELECT * FROM test_external_runners")
    print(f"Found {len(rows)} row(s).")
    assert len(rows) >= 1

    # 7. Clean up
    print("\n[Step 7] Dropping test table...")
    await mgr.execute("DROP TABLE test_external_runners")
    print("Table dropped.")

    # 8. Close connection pool
    print("\n[Step 8] Closing connection pool...")
    await mgr.close_pool()
    print("Connection pool closed.")

    print("\n" + "=" * 60)
    print("ALL EXTERNAL DB TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_tests())
