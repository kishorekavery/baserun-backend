import asyncio
import os
import sys
from pathlib import Path

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# Force load latest .env
load_dotenv(override=True)

from pwdlib import PasswordHash
from db.config import DatabaseConfig
from db.external_db import ExternalDatabaseManager

pwd_hasher = PasswordHash.recommended()


CREATE_TABLES_SQL = """
-- 1. Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT,
    phone TEXT,
    territory_color TEXT DEFAULT '#00E5FF',
    avatar_url TEXT DEFAULT 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=250&q=80',
    city TEXT DEFAULT 'Chennai, India',
    club_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Telemetry points table
CREATE TABLE IF NOT EXISTS telemetry_points (
    id SERIAL PRIMARY KEY,
    activity_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    altitude REAL,
    speed REAL,
    accuracy REAL,
    timestamp TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_telemetry_activity ON telemetry_points(activity_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_user ON telemetry_points(user_id);

-- 3. Claimed bases / territories table
CREATE TABLE IF NOT EXISTS claimed_bases (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'Territory',
    user_id TEXT NOT NULL,
    club_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    area REAL NOT NULL,
    perimeter REAL NOT NULL,
    polygon_wkt TEXT NOT NULL,
    color TEXT DEFAULT '#00E5FF',
    total_runs INTEGER DEFAULT 1,
    total_km REAL DEFAULT 0.0,
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bases_user ON claimed_bases(user_id);

-- 4. Runs table
CREATE TABLE IF NOT EXISTS runs (
    id SERIAL PRIMARY KEY,
    activity_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    title TEXT DEFAULT 'City Run',
    status TEXT NOT NULL DEFAULT 'in_progress',
    distance_km REAL DEFAULT 0.0,
    duration_seconds INTEGER DEFAULT 0,
    pace_min_per_km TEXT DEFAULT '0''00"',
    steps INTEGER DEFAULT 0,
    calories INTEGER DEFAULT 0,
    avg_speed_kmh REAL DEFAULT 0.0,
    elevation_gain_m REAL DEFAULT 0.0,
    start_time TEXT NOT NULL,
    end_time TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runs_activity ON runs(activity_id);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id);

-- 5. Clubs table
CREATE TABLE IF NOT EXISTS clubs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    avatar_url TEXT,
    color TEXT DEFAULT '#E91E63',
    member_count INTEGER DEFAULT 1,
    territory_count INTEGER DEFAULT 0,
    total_km REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Club Members table
CREATE TABLE IF NOT EXISTS club_members (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT DEFAULT 'member',
    territories_contributed INTEGER DEFAULT 0,
    km_contributed REAL DEFAULT 0.0,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(club_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_club_members_club ON club_members(club_id);
CREATE INDEX IF NOT EXISTS idx_club_members_user ON club_members(user_id);

-- 7. Achievements table
CREATE TABLE IF NOT EXISTS achievements (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    badge_icon TEXT NOT NULL,
    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id);
"""

SAMPLE_CLUBS = [
    {
        "id": 1,
        "name": "Chennai Runners",
        "description": "A community of runners exploring and claiming Chennai, one run at a time.",
        "avatar_url": "https://images.unsplash.com/photo-1552674605-db6ffd4facb5?auto=format&fit=crop&w=150&q=80",
        "color": "#E91E63",
        "member_count": 245,
        "territory_count": 12,
        "total_km": 1420.5,
    },
    {
        "id": 2,
        "name": "Anna Nagar Runners",
        "description": "West Chennai's most active turf-claiming morning club.",
        "avatar_url": "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?auto=format&fit=crop&w=150&q=80",
        "color": "#FF9100",
        "member_count": 120,
        "territory_count": 6,
        "total_km": 820.0,
    },
    {
        "id": 3,
        "name": "Marina Striders",
        "description": "Coastal runners patrolling the beaches from Lighthouse to Napier Bridge.",
        "avatar_url": "https://images.unsplash.com/photo-1476480862126-209bfaa8edc8?auto=format&fit=crop&w=150&q=80",
        "color": "#00E5FF",
        "member_count": 180,
        "territory_count": 8,
        "total_km": 1150.2,
    },
    {
        "id": 4,
        "name": "Besant Nagar Beach Club",
        "description": "Bessie beach sprint squad conquering coastal turf.",
        "avatar_url": "https://images.unsplash.com/photo-1502680390469-be75c86b636f?auto=format&fit=crop&w=150&q=80",
        "color": "#76FF03",
        "member_count": 95,
        "territory_count": 4,
        "total_km": 540.8,
    },
]

USERS_TO_SEED = [
    {
        "username": "speedy_alex",
        "raw_password": "Runner123!",
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "alex.runner@example.com",
        "phone": "+919876543210",
        "territory_color": "#00E5FF",  # Neon Cyan
        "avatar_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=250&q=80",
        "city": "Chennai, India",
        "club_id": 1,
    },
    {
        "username": "marina_strider",
        "raw_password": "Runner123!",
        "first_name": "Priya",
        "last_name": "Raman",
        "email": "priya.run@example.com",
        "phone": "+919876543211",
        "territory_color": "#E91E63",  # Electric Magenta
        "avatar_url": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=250&q=80",
        "city": "Chennai, India",
        "club_id": 3,
    },
    {
        "username": "karthik_pace",
        "raw_password": "Runner123!",
        "first_name": "Karthik",
        "last_name": "Sunder",
        "email": "karthik.p@example.com",
        "phone": "+919876543212",
        "territory_color": "#FF9100",  # Solar Orange
        "avatar_url": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=250&q=80",
        "city": "Chennai, India",
        "club_id": 2,
    },
    {
        "username": "volt_meena",
        "raw_password": "Runner123!",
        "first_name": "Meena",
        "last_name": "Kumari",
        "email": "meena.volt@example.com",
        "phone": "+919876543213",
        "territory_color": "#76FF03",  # Volt Lime
        "avatar_url": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?auto=format&fit=crop&w=250&q=80",
        "city": "Chennai, India",
        "club_id": 4,
    },
    {
        "username": "vikram_turf",
        "raw_password": "Runner123!",
        "first_name": "Vikram",
        "last_name": "Aditya",
        "email": "vikram.t@example.com",
        "phone": "+919876543214",
        "territory_color": "#7C4DFF",  # Deep Purple
        "avatar_url": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=250&q=80",
        "city": "Chennai, India",
        "club_id": 1,
    },
]


async def seed():
    cfg = DatabaseConfig.from_env()
    print("=" * 60)
    print("SEEDING EXTERNAL SUPABASE POSTGRESQL DATABASE")
    print("=" * 60)
    print(f"Target Host: {cfg.external_host}:{cfg.external_port}")
    print(f"Database: {cfg.external_name}")
    print(f"User: {cfg.external_user}")

    mgr = ExternalDatabaseManager(config=cfg)
    ok = await mgr.init_pool()
    if not ok:
        print("ERROR: Could not initialize connection pool to external DB.")
        return

    # 1. Create Tables
    print("\n[Step 1] Creating schema tables in PostgreSQL...")
    await mgr.execute(CREATE_TABLES_SQL)
    print("All schema tables successfully verified/created.")

    # 2. Seed Clubs
    print("\n[Step 2] Seeding Running Clubs...")
    for club in SAMPLE_CLUBS:
        await mgr.execute(
            """
            INSERT INTO clubs (id, name, description, avatar_url, color, member_count, territory_count, total_km)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                avatar_url = EXCLUDED.avatar_url,
                color = EXCLUDED.color,
                member_count = EXCLUDED.member_count,
                territory_count = EXCLUDED.territory_count,
                total_km = EXCLUDED.total_km;
            """,
            club["id"],
            club["name"],
            club["description"],
            club["avatar_url"],
            club["color"],
            club["member_count"],
            club["territory_count"],
            club["total_km"],
        )
    print(f"Seeded {len(SAMPLE_CLUBS)} clubs.")

    # 3. Seed Users
    print("\n[Step 3] Seeding 5 Users with Argon2 password hashes...")
    user_id_map = {}
    for u in USERS_TO_SEED:
        hashed = pwd_hasher.hash(u["raw_password"])
        # Check if user already exists
        existing = await mgr.fetch_one("SELECT id FROM users WHERE username = $1", u["username"])
        if existing:
            user_id = existing["id"]
            await mgr.execute(
                """
                UPDATE users SET
                    hashed_password = $1,
                    first_name = $2,
                    last_name = $3,
                    email = $4,
                    phone = $5,
                    territory_color = $6,
                    avatar_url = $7,
                    city = $8,
                    club_id = $9
                WHERE id = $10;
                """,
                hashed,
                u["first_name"],
                u["last_name"],
                u["email"],
                u["phone"],
                u["territory_color"],
                u["avatar_url"],
                u["city"],
                u["club_id"],
                user_id,
            )
        else:
            row = await mgr.fetch_one(
                """
                INSERT INTO users (username, email, hashed_password, first_name, last_name, phone, territory_color, avatar_url, city, club_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id;
                """,
                u["username"],
                u["email"],
                hashed,
                u["first_name"],
                u["last_name"],
                u["phone"],
                u["territory_color"],
                u["avatar_url"],
                u["city"],
                u["club_id"],
            )
            user_id = row["id"]

        user_id_map[u["username"]] = user_id
        print(f" -> User '{u['username']}' (ID: {user_id}) password set to '{u['raw_password']}'")

    # 4. Seed Club Memberships
    print("\n[Step 4] Seeding Club Memberships...")
    for u in USERS_TO_SEED:
        uid = str(user_id_map[u["username"]])
        cid = u["club_id"]
        await mgr.execute(
            """
            INSERT INTO club_members (club_id, user_id, role, territories_contributed, km_contributed)
            VALUES ($1, $2, 'member', 2, 28.5)
            ON CONFLICT (club_id, user_id) DO UPDATE SET
                territories_contributed = EXCLUDED.territories_contributed,
                km_contributed = EXCLUDED.km_contributed;
            """,
            cid,
            uid,
        )
    print("Club memberships recorded.")

    # 5. Seed Test Covered Regions (Claimed Bases)
    print("\n[Step 5] Seeding Test Covered Regions / Territories...")
    sample_territories = [
        {
            "name": "Marina Promenade Loop",
            "owner": "speedy_alex",
            "club_id": 1,
            "area": 2.45,
            "perimeter": 6.32,
            "color": "#00E5FF",
            "polygon_wkt": "POLYGON ((80.2785 13.0500, 80.2850 13.0500, 80.2840 13.0650, 80.2770 13.0650, 80.2785 13.0500))",
            "total_runs": 8,
            "total_km": 42.5,
        },
        {
            "name": "Anna Nagar Tower Park Turf",
            "owner": "karthik_pace",
            "club_id": 2,
            "area": 1.82,
            "perimeter": 5.40,
            "color": "#FF9100",
            "polygon_wkt": "POLYGON ((80.2100 13.0850, 80.2200 13.0850, 80.2200 13.0950, 80.2100 13.0950, 80.2100 13.0850))",
            "total_runs": 5,
            "total_km": 26.8,
        },
        {
            "name": "Besant Nagar Coastal Turf",
            "owner": "volt_meena",
            "club_id": 4,
            "area": 3.15,
            "perimeter": 7.80,
            "color": "#76FF03",
            "polygon_wkt": "POLYGON ((80.2650 12.9950, 80.2750 12.9950, 80.2740 13.0100, 80.2640 13.0100, 80.2650 12.9950))",
            "total_runs": 11,
            "total_km": 68.2,
        },
        {
            "name": "Guindy Park Circuit",
            "owner": "vikram_turf",
            "club_id": 1,
            "area": 2.90,
            "perimeter": 6.95,
            "color": "#7C4DFF",
            "polygon_wkt": "POLYGON ((80.2200 13.0050, 80.2320 13.0050, 80.2310 13.0180, 80.2190 13.0180, 80.2200 13.0050))",
            "total_runs": 7,
            "total_km": 38.0,
        },
        {
            "name": "Santhome Beach Turf",
            "owner": "marina_strider",
            "club_id": 3,
            "area": 1.50,
            "perimeter": 4.90,
            "color": "#E91E63",
            "polygon_wkt": "POLYGON ((80.2760 13.0300, 80.2830 13.0300, 80.2820 13.0420, 80.2750 13.0420, 80.2760 13.0300))",
            "total_runs": 4,
            "total_km": 19.5,
        },
    ]

    for t in sample_territories:
        owner_id = str(user_id_map[t["owner"]])
        # Delete existing territory with same name to refresh
        await mgr.execute("DELETE FROM claimed_bases WHERE name = $1", t["name"])
        await mgr.execute(
            """
            INSERT INTO claimed_bases (name, user_id, club_id, status, area, perimeter, polygon_wkt, color, total_runs, total_km)
            VALUES ($1, $2, $3, 'active', $4, $5, $6, $7, $8, $9);
            """,
            t["name"],
            owner_id,
            t["club_id"],
            t["area"],
            t["perimeter"],
            t["polygon_wkt"],
            t["color"],
            t["total_runs"],
            t["total_km"],
        )
        print(f" -> Territory '{t['name']}' ({t['area']} km²) assigned to {t['owner']}")

    # 6. Seed Achievements
    print("\n[Step 6] Seeding Sample Achievements...")
    for username, uid in user_id_map.items():
        await mgr.execute("DELETE FROM achievements WHERE user_id = $1", str(uid))
        await mgr.execute(
            """
            INSERT INTO achievements (user_id, title, description, badge_icon)
            VALUES
                ($1, 'First Turf Conquered', 'Claimed your very first territory loop.', 'badge_turf_1'),
                ($1, '10K Coastal Cruiser', 'Logged over 10km in coastal running sessions.', 'badge_coastal_10k');
            """,
            str(uid),
        )
    print("Achievements seeded.")

    # 7. Verification Summary
    print("\n" + "=" * 60)
    print("VERIFICATION & SEEDING COMPLETED SUCCESSFULLY!")
    print("=" * 60)
    user_rows = await mgr.fetch_all("SELECT id, username, email, territory_color, city, club_id FROM users ORDER BY id;")
    print(f"\nTotal Users in External DB: {len(user_rows)}")
    for r in user_rows:
        print(f"  [ID {r['id']}] @{r['username']} | {r['email']} | Color: {r['territory_color']} | Club: {r['club_id']}")

    club_rows = await mgr.fetch_all("SELECT id, name, color, member_count, territory_count FROM clubs ORDER BY id;")
    print(f"\nTotal Clubs in External DB: {len(club_rows)}")
    for c in club_rows:
        print(f"  [Club {c['id']}] {c['name']} | Color: {c['color']} | Members: {c['member_count']}")

    territory_rows = await mgr.fetch_all("SELECT id, name, user_id, area, color FROM claimed_bases ORDER BY id;")
    print(f"\nTotal Covered Regions in External DB: {len(territory_rows)}")
    for t in territory_rows:
        print(f"  [Territory {t['id']}] '{t['name']}' | Area: {t['area']} km² | Owner ID: {t['user_id']}")

    await mgr.close_pool()


if __name__ == "__main__":
    asyncio.run(seed())
