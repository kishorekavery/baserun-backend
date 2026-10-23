"""
Seeds demo runners and one territory each into the configured PostgreSQL
database (DATABASE_URL / EXTERNAL_DB_* from .env). Safe to run repeatedly.

    python scripts/seed_demo_data.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from pwdlib import PasswordHash  # noqa: E402

from db.database import close_db, transaction  # noqa: E402
from services.territory_service import insert_territory  # noqa: E402
from utils.geo import polygon_from_points  # noqa: E402

DEMO_PASSWORD = os.getenv("DEMO_USER_PASSWORD", "Runner123!")

# username, first, last, email, color, club name, territory name, loop south-west corner
DEMO_RUNNERS = [
    ("speedy_alex", "Alex", "Morgan", "alex.runner@example.com", "#00E5FF", "Chennai Runners",
     "Marina Beach (North)", (13.0590, 80.2810)),
    ("marina_strider", "Priya", "Raman", "priya.run@example.com", "#FF007F", "Marina Striders",
     "Lighthouse Loop", (13.0390, 80.2790)),
    ("karthik_pace", "Karthik", "Sunder", "karthik.p@example.com", "#FF5722", "Anna Nagar Runners",
     "Anna Nagar Tower Park", (13.0850, 80.2100)),
    ("volt_meena", "Meena", "Kumari", "meena.volt@example.com", "#76FF03", "Marina Striders",
     "Besant Nagar Promenade", (12.9990, 80.2690)),
    ("vikram_turf", "Vikram", "Aditya", "vikram.t@example.com", "#7928CA", "Chennai Runners",
     "Nungambakkam Circuit", (13.0600, 80.2420)),
]
LOOP_SIZE_DEG = 0.004  # ~440 m sides


async def seed() -> None:
    hashed = PasswordHash.recommended().hash(DEMO_PASSWORD)
    async with transaction() as conn:
        for username, first, last, email, color, club_name, territory, (lat, lng) in DEMO_RUNNERS:
            club_id = await conn.fetchval("SELECT id FROM clubs WHERE name = $1", club_name)
            user_id = await conn.fetchval("SELECT id FROM users WHERE LOWER(username) = LOWER($1)", username)
            if user_id is None:
                user_id = await conn.fetchval(
                    """
                    INSERT INTO users (username, email, hashed_password, first_name, last_name, territory_color, club_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
                    """,
                    username, email, hashed, first, last, color, club_id,
                )
                print(f"Created runner {username} (id={user_id})")
            if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM territories WHERE owner_id = $1)", user_id):
                continue
            corners = [(lat, lng), (lat + LOOP_SIZE_DEG, lng), (lat + LOOP_SIZE_DEG, lng + LOOP_SIZE_DEG),
                       (lat, lng + LOOP_SIZE_DEG)]
            metrics = polygon_from_points(corners)
            await insert_territory(
                conn, name=territory, owner_id=user_id, club_id=club_id, run_id=None,
                metrics=metrics, record_pace_seconds=360, total_km=metrics.perimeter_km,
            )
            print(f"  + territory {territory} ({metrics.area_km2} km²)")
    await close_db()
    print("Demo data ready.")


if __name__ == "__main__":
    asyncio.run(seed())
