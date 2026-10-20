-- ====================================================================
-- Base Run: Run to Claim Database Schema (SQLite)
-- Supports all 12 mobile UI screens:
-- 1. Splash Screen
-- 2. Login
-- 3. Register
-- 4. Username & Color (Choose Your Identity)
-- 5. Location Permission & Nearby Territory Preview
-- 6. Home Map
-- 7. Run In Progress (Live Tracking)
-- 8. Run Summary
-- 9. Territory Claimed
-- 10. Territory Details & Challenge
-- 11. Club Leaderboard
-- 12. User Profile, Stats & Achievements
-- ====================================================================

-- 1. Users table
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
);

-- 2. Telemetry points table for in-run GPS tracking
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
);
CREATE INDEX IF NOT EXISTS idx_telemetry_activity ON telemetry_points(activity_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_user ON telemetry_points(user_id);

-- 3. Claimed bases / territories table
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
);
CREATE INDEX IF NOT EXISTS idx_bases_user ON claimed_bases(user_id);

-- 4. Runs / Activities table
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
    is_claimed INTEGER DEFAULT 0,
    current_latitude REAL,
    current_longitude REAL,
    last_telemetry_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runs_activity ON runs(activity_id);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id);

-- 5. Clubs table
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
);

-- 6. Club Members table
CREATE TABLE IF NOT EXISTS club_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT DEFAULT 'member',
    territories_contributed INTEGER DEFAULT 0,
    km_contributed REAL DEFAULT 0.0,
    joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(club_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_club_members_club ON club_members(club_id);
CREATE INDEX IF NOT EXISTS idx_club_members_user ON club_members(user_id);

-- 7. Achievements table
CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    badge_icon TEXT NOT NULL,
    unlocked_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id);

-- 8. Sessions table for server-side token management & revocation
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    is_revoked INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_id ON sessions(session_id);

