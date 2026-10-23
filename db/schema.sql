-- ====================================================================
-- BASER (Run to Claim) — PostgreSQL schema
-- Idempotent: safe to run on every cold start.
-- Geometry is computed in Python (shapely + pyproj geodesics) and stored
-- as WKT with a bounding box, so plain PostgreSQL is enough (no PostGIS).
-- ====================================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 1. Seasons
CREATE TABLE IF NOT EXISTS seasons (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Clubs (member / territory counts are aggregated live, never stored)
CREATE TABLE IF NOT EXISTS clubs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    tagline VARCHAR(255),
    description TEXT,
    avatar_url TEXT,
    color VARCHAR(7) NOT NULL DEFAULT '#E91E63',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Users
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(32) NOT NULL,
    email VARCHAR(255) NOT NULL,
    hashed_password TEXT NOT NULL,
    first_name VARCHAR(64) NOT NULL DEFAULT 'Runner',
    last_name VARCHAR(64),
    phone VARCHAR(32),
    territory_color VARCHAR(7) NOT NULL DEFAULT '#00E5FF',
    avatar_url TEXT,
    bio TEXT,
    city VARCHAR(120) DEFAULT 'Chennai, India',
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    auth_provider VARCHAR(20) NOT NULL DEFAULT 'password',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users (LOWER(username));
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (LOWER(email));
CREATE INDEX IF NOT EXISTS idx_users_club ON users (club_id);

-- 4. Server-side sessions (JWT jti -> session row)
CREATE TABLE IF NOT EXISTS sessions (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

-- 5. Runs / activities. activity_id is TEXT because offline clients mint their own ids.
CREATE TABLE IF NOT EXISTS runs (
    id BIGSERIAL PRIMARY KEY,
    activity_id VARCHAR(128) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(120) NOT NULL DEFAULT 'City Run',
    status VARCHAR(20) NOT NULL DEFAULT 'recording'
        CHECK (status IN ('recording', 'paused', 'completed')),
    route_mode VARCHAR(32) NOT NULL DEFAULT 'free_run',
    target_territory_id INTEGER,
    device_info VARCHAR(255),
    is_recovered BOOLEAN NOT NULL DEFAULT FALSE,
    start_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    end_time TIMESTAMPTZ,
    paused_at TIMESTAMPTZ,
    total_paused_seconds INTEGER NOT NULL DEFAULT 0,
    start_latitude DOUBLE PRECISION,
    start_longitude DOUBLE PRECISION,
    current_latitude DOUBLE PRECISION,
    current_longitude DOUBLE PRECISION,
    last_telemetry_at TIMESTAMPTZ,
    distance_km DOUBLE PRECISION NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    avg_pace_seconds INTEGER NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    calories INTEGER NOT NULL DEFAULT 0,
    avg_speed_kmh DOUBLE PRECISION NOT NULL DEFAULT 0,
    elevation_gain_m DOUBLE PRECISION NOT NULL DEFAULT 0,
    is_loop_closed BOOLEAN NOT NULL DEFAULT FALSE,
    loop_area_km2 DOUBLE PRECISION NOT NULL DEFAULT 0,
    loop_polygon_wkt TEXT,
    is_claimed BOOLEAN NOT NULL DEFAULT FALSE,
    claim_outcome VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status, last_telemetry_at);

-- 6. GPS telemetry
CREATE TABLE IF NOT EXISTS telemetry_points (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    altitude DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    accuracy DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_run ON telemetry_points (run_id, id);

-- 7. Territories (bases)
CREATE TABLE IF NOT EXISTS territories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    origin_run_id BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'unclaimed')),
    area_km2 DOUBLE PRECISION NOT NULL,
    perimeter_km DOUBLE PRECISION NOT NULL,
    polygon_wkt TEXT NOT NULL,
    min_lat DOUBLE PRECISION NOT NULL,
    min_lng DOUBLE PRECISION NOT NULL,
    max_lat DOUBLE PRECISION NOT NULL,
    max_lng DOUBLE PRECISION NOT NULL,
    centroid_lat DOUBLE PRECISION NOT NULL,
    centroid_lng DOUBLE PRECISION NOT NULL,
    defense_level INTEGER NOT NULL DEFAULT 1,
    record_pace_seconds INTEGER,
    times_defended INTEGER NOT NULL DEFAULT 0,
    times_challenged INTEGER NOT NULL DEFAULT 0,
    total_runs INTEGER NOT NULL DEFAULT 1,
    total_km DOUBLE PRECISION NOT NULL DEFAULT 0,
    conquered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_defended_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_territories_owner ON territories (owner_id);
CREATE INDEX IF NOT EXISTS idx_territories_club ON territories (club_id);
CREATE INDEX IF NOT EXISTS idx_territories_bbox ON territories (min_lat, max_lat, min_lng, max_lng);

-- 8. Challenges
CREATE TABLE IF NOT EXISTS challenges (
    id SERIAL PRIMARY KEY,
    challenge_id VARCHAR(40) NOT NULL UNIQUE,
    territory_id INTEGER NOT NULL REFERENCES territories(id) ON DELETE CASCADE,
    challenger_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'won', 'lost')),
    target_pace_seconds INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_challenges_territory ON challenges (territory_id, challenger_id, status);

-- 9. Achievement catalog + unlocks
CREATE TABLE IF NOT EXISTS achievements (
    id SERIAL PRIMARY KEY,
    key VARCHAR(50) NOT NULL UNIQUE,
    title VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    badge_icon VARCHAR(50) NOT NULL DEFAULT 'trophy'
);

CREATE TABLE IF NOT EXISTS user_achievements (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    achievement_id INTEGER NOT NULL REFERENCES achievements(id) ON DELETE CASCADE,
    unlocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, achievement_id)
);
