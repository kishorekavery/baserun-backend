"""
Read-only connectivity check for the configured PostgreSQL database.
Applies the (idempotent) schema on first connect, then prints health and row counts.

    python scripts/check_db.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from db.config import db_config  # noqa: E402
from db.database import check_db_health, close_db, connection  # noqa: E402

TABLES = ["users", "clubs", "runs", "telemetry_points", "territories", "challenges", "user_achievements", "sessions"]


async def main() -> int:
    print("Config:", db_config.to_dict(mask_secrets=True))
    health = await check_db_health()
    print("Health:", health)
    if health["connection"] != "ok":
        return 1
    async with connection() as conn:
        for table in TABLES:
            print(f"  {table:<18} {await conn.fetchval(f'SELECT count(*) FROM {table}')}")
    await close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
