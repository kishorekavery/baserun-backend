import os
import aiosqlite
from db.config import db_config, DatabaseConfig
from db.external_db import (
    external_db,
    init_external_db,
    close_external_db,
    check_external_db_health,
)

DATABASE_PATH = os.getenv("DATABASE_PATH", db_config.database_path)

# --------- Init DB ---------
async def init_db():
    dir_name = os.path.dirname(os.path.abspath(DATABASE_PATH))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        # 1. Users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Safely migrate users table columns if existing DB
        for col_def in [
            "territory_color TEXT DEFAULT '#00E5FF'",
            "avatar_url TEXT DEFAULT 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=250&q=80'",
            "city TEXT DEFAULT 'Chennai, India'",
            "club_id INTEGER",
        ]:
            try:
                await conn.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
            except Exception:
                pass

        # 2. Telemetry points table for in-run GPS tracking
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                altitude REAL,
                speed REAL,
                accuracy REAL,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_activity ON telemetry_points(activity_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_user ON telemetry_points(user_id)")

        # 3. Claimed bases / territories table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS claimed_bases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                last_active_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bases_user ON claimed_bases(user_id)")

        for col_def in [
            "name TEXT NOT NULL DEFAULT 'Territory'",
            "club_id INTEGER",
            "color TEXT DEFAULT '#00E5FF'",
            "total_runs INTEGER DEFAULT 1",
            "total_km REAL DEFAULT 0.0",
            "last_active_at TEXT DEFAULT CURRENT_TIMESTAMP",
        ]:
            try:
                await conn.execute(f"ALTER TABLE claimed_bases ADD COLUMN {col_def}")
            except Exception:
                pass

        # 4. Runs / Activities table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_activity ON runs(activity_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)")

        # 5. Clubs table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clubs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                avatar_url TEXT,
                color TEXT DEFAULT '#E91E63',
                member_count INTEGER DEFAULT 1,
                territory_count INTEGER DEFAULT 0,
                total_km REAL DEFAULT 0.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 6. Club Members table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS club_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                club_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT DEFAULT 'member',
                territories_contributed INTEGER DEFAULT 0,
                km_contributed REAL DEFAULT 0.0,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(club_id, user_id)
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_club_members_club ON club_members(club_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_club_members_user ON club_members(user_id)")

        # 7. Achievements table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                badge_icon TEXT NOT NULL,
                unlocked_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id)")

        # Seed sample clubs if empty
        async with conn.execute("SELECT count(*) as cnt FROM clubs") as cur:
            row = await cur.fetchone()
            if row and row[0] == 0:
                await conn.executemany("""
                    INSERT INTO clubs (id, name, description, avatar_url, color, member_count, territory_count, total_km)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (1, "Chennai Runners", "A community of runners exploring and claiming Chennai, one run at a time.", "https://images.unsplash.com/photo-1552674605-db6ffd4facb5?auto=format&fit=crop&w=150&q=80", "#E91E63", 245, 12, 1420.5),
                    (2, "Anna Nagar Runners", "West Chennai's most active turf-claiming morning club.", "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?auto=format&fit=crop&w=150&q=80", "#FF9800", 120, 6, 820.0),
                    (3, "Marina Striders", "Coastal runners patrolling the beaches from Lighthouse to Napier Bridge.", "https://images.unsplash.com/photo-1476480862126-209bfaa8edc8?auto=format&fit=crop&w=150&q=80", "#2196F3", 180, 8, 1150.2),
                ])

        await conn.commit()


# ------- SQLite Connection Helper ------
async def sqlite_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DATABASE_PATH)
    conn.row_factory = aiosqlite.Row
    return conn

# ----- Database helper functions -----
async def insert_records(conn: aiosqlite.Connection, table_name: str, columns: list[str], data: list) -> bool:
    placeholders = ', '.join(['?'] * len(columns))
    cols_str = ', '.join(columns)
    query = f"INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})"
    async with conn.cursor() as cur:
        await cur.execute(query, data)
        await conn.commit()
        return True

async def insert_record_get_id(conn: aiosqlite.Connection, table_name: str, columns: list[str], data: list) -> int:
    placeholders = ', '.join(['?'] * len(columns))
    cols_str = ', '.join(columns)
    query = f"INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})"
    async with conn.cursor() as cur:
        await cur.execute(query, data)
        await conn.commit()
        return cur.lastrowid

async def insert_many_records(conn: aiosqlite.Connection, table_name: str, columns: list[str], data_list: list[list]) -> bool:
    if not data_list:
        return True
    placeholders = ', '.join(['?'] * len(columns))
    cols_str = ', '.join(columns)
    query = f"INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})"
    async with conn.cursor() as cur:
        await cur.executemany(query, data_list)
        await conn.commit()
        return True

async def fetch_record(conn: aiosqlite.Connection, table_name: str, column: str, value: tuple):
    query = f"SELECT * FROM {table_name} WHERE {column} = ?"
    async with conn.execute(query, value) as cursor:
        return await cursor.fetchone()

async def fetch_records(conn: aiosqlite.Connection, table_name: str, column: str, value: tuple):
    query = f"SELECT * FROM {table_name} WHERE {column} = ?"
    async with conn.execute(query, value) as cursor:
        return await cursor.fetchall()

async def update_record(conn: aiosqlite.Connection, table_name: str, column: str, data: tuple, condition: str):
    query = f"UPDATE {table_name} SET {column} = ? WHERE {condition}"
    async with conn.cursor() as cur:
        await cur.execute(query, data)
        await conn.commit()
        return True

async def execute_query(conn: aiosqlite.Connection, query: str, params: tuple = ()):
    async with conn.execute(query, params) as cursor:
        return await cursor.fetchall()

async def execute_query_one(conn: aiosqlite.Connection, query: str, params: tuple = ()):
    async with conn.execute(query, params) as cursor:
        return await cursor.fetchone()