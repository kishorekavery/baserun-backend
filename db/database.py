"""
PostgreSQL access layer (asyncpg).

* One connection pool per event loop. Serverless runtimes and test clients
  may run requests on different loops, and an asyncpg pool is bound to the
  loop that created it.
* The schema is applied lazily on first use (and from the app lifespan), so
  cold starts on Vercel work without a separate migration step.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import asyncpg

from db.config import DatabaseConfig, db_config

logger = logging.getLogger("baserun.db")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = 1
# Set DB_AUTO_MIGRATE=false when the app role may not run DDL (apply db/schema.sql manually)
AUTO_MIGRATE = os.getenv("DB_AUTO_MIGRATE", "true").strip().lower() not in {"0", "false", "no"}

# Tables created by the pre-PostgreSQL code base. If they exist without a
# schema_migrations table they are renamed to legacy_<name> (data is kept).
LEGACY_TABLES = [
    "users", "telemetry_points", "claimed_bases", "runs", "clubs",
    "club_members", "achievements", "sessions",
]

DEFAULT_CLUBS = [
    ("Chennai Runners", "Claiming Chennai, one run at a time",
     "A community of runners exploring and claiming Chennai, one run at a time.",
     "https://images.unsplash.com/photo-1552674605-db6ffd4facb5?auto=format&fit=crop&w=150&q=80", "#E91E63"),
    ("Anna Nagar Runners", "West Chennai's morning crew",
     "West Chennai's most active turf-claiming morning club.",
     "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?auto=format&fit=crop&w=150&q=80", "#FF9800"),
    ("Marina Striders", "Ruling the coastline",
     "Coastal runners patrolling the beaches from Lighthouse to Napier Bridge.",
     "https://images.unsplash.com/photo-1476480862126-209bfaa8edc8?auto=format&fit=crop&w=150&q=80", "#2196F3"),
]

DEFAULT_ACHIEVEMENTS = [
    ("first_run", "First Steps", "Completed your first run", "directions_run"),
    ("first_conquest", "First Conquest", "Claimed your first base territory", "trophy"),
    ("5k_conqueror", "5K Conqueror", "Claimed a base with a run of 5 km or more", "military_tech"),
    ("10k_club", "10K Club", "Ran over 10 km in a single session", "military_tech"),
    ("club_hero", "Club Hero", "Held 5 bases for your running club", "shield"),
    ("hostile_takeover", "Hostile Takeover", "Captured a base from a rival runner", "bolt"),
]

DEFAULT_SEASON = ("Season 1: Chennai Conquest", datetime(2026, 1, 1, tzinfo=timezone.utc))


class _PoolState:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.lock: Optional[asyncio.Lock] = None
        self.schema_ready = False


_state = _PoolState()


async def _create_pool(config: DatabaseConfig) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=config.get_database_url(),
        min_size=min(config.min_connections, config.max_connections),
        max_size=config.max_connections,
        command_timeout=config.timeout,
        timeout=config.timeout,
        # Supabase/pgbouncer pooling does not support named prepared statements
        statement_cache_size=0,
    )


async def get_pool() -> asyncpg.Pool:
    loop = asyncio.get_running_loop()
    if _state.loop is not loop:
        stale_pool = _state.pool
        _state.pool, _state.loop, _state.lock = None, loop, asyncio.Lock()
        if stale_pool is not None:
            try:
                stale_pool.terminate()
            except Exception:  # its loop is already gone
                pass

    if _state.pool is None:
        async with _state.lock:
            if _state.pool is None:
                pool = await _create_pool(db_config)
                try:
                    if not _state.schema_ready and AUTO_MIGRATE:
                        async with pool.acquire() as conn:
                            await apply_schema(conn)
                    _state.schema_ready = True
                except BaseException:
                    await pool.close()
                    raise
                _state.pool = pool
    return _state.pool


async def init_db() -> None:
    """Create the pool and make sure the schema exists (app startup)."""
    await get_pool()


async def close_db() -> None:
    pool, _state.pool, _state.loop = _state.pool, None, None
    if pool is not None:
        try:
            await pool.close()
        except Exception as exc:
            logger.warning("Error while closing database pool: %s", exc)


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with connection() as conn:
        async with conn.transaction():
            yield conn


# ------------------------------------------------------------------
# Schema management
# ------------------------------------------------------------------

async def _rename_legacy_tables(conn: asyncpg.Connection) -> None:
    has_migrations = await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
    if has_migrations:
        return
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for table in LEGACY_TABLES:
        exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")
        if not exists:
            continue
        target = f"legacy_{table}"
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{target}"):
            target = f"legacy_{table}_{suffix}"
        logger.warning("Renaming pre-PostgreSQL table %s to %s", table, target)
        await conn.execute(f'ALTER TABLE public."{table}" RENAME TO "{target}"')
        # Free the index names so the new schema can reuse them
        index_names = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = $1",
            target,
        )
        for row in index_names:
            name = row["indexname"]
            if name.startswith("idx_"):
                await conn.execute(f'ALTER INDEX public."{name}" RENAME TO "legacy_{suffix}_{name}"')


async def _seed_reference_data(conn: asyncpg.Connection) -> None:
    await conn.executemany(
        """
        INSERT INTO achievements (key, title, description, badge_icon)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (key) DO UPDATE
        SET title = EXCLUDED.title, description = EXCLUDED.description, badge_icon = EXCLUDED.badge_icon
        WHERE (achievements.title, achievements.description, achievements.badge_icon)
              IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.description, EXCLUDED.badge_icon)
        """,
        DEFAULT_ACHIEVEMENTS,
    )
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM clubs)"):
        await conn.executemany(
            """
            INSERT INTO clubs (name, tagline, description, avatar_url, color)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (name) DO NOTHING
            """,
            DEFAULT_CLUBS,
        )
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM seasons)"):
        await conn.execute(
            "INSERT INTO seasons (name, starts_at) VALUES ($1, $2) ON CONFLICT (name) DO NOTHING",
            *DEFAULT_SEASON,
        )


async def apply_schema(conn: asyncpg.Connection) -> None:
    """Idempotently create tables, indexes and reference rows."""
    async with conn.transaction():
        # Serialise concurrent cold starts
        await conn.execute("SELECT pg_advisory_xact_lock(727274)")
        await _rename_legacy_tables(conn)
        await conn.execute(SCHEMA_PATH.read_text())
        await conn.execute(
            "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT (version) DO NOTHING",
            SCHEMA_VERSION,
        )
        await _seed_reference_data(conn)


# ------------------------------------------------------------------
# Maintenance & health
# ------------------------------------------------------------------

async def cleanup_expired_sessions(retention: timedelta = timedelta(days=1)) -> int:
    """
    Deletes sessions that expired more than `retention` ago.
    Returns the number of deleted rows.
    """
    async with connection() as conn:
        result = await conn.execute(
            "DELETE FROM sessions WHERE expires_at < $1",
            datetime.now(timezone.utc) - retention,
        )
    return int(result.split()[-1])


async def check_db_health() -> Dict[str, Any]:
    start = time.perf_counter()
    try:
        async with connection() as conn:
            active_sessions = await conn.fetchval(
                "SELECT count(*) FROM sessions WHERE is_revoked = FALSE AND expires_at > NOW()"
            )
        return {
            "database": "Ready",
            "connection": "ok",
            "active_sessions": active_sessions,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return {
            "database": "Unavailable",
            "connection": "error",
            "error": type(exc).__name__,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }
