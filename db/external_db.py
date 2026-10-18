import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional

from db.config import DatabaseConfig, db_config

logger = logging.getLogger("baserun.external_db")

# Attempt importing asyncpg for asynchronous PostgreSQL connection pooling
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False
    logger.warning("asyncpg is not installed. External PostgreSQL connection pool will not be active.")


class ExternalDatabaseManager:
    """
    Manages connection pooling and query execution for external databases (PostgreSQL/Supabase/RDS).
    """

    def __init__(self, config: Optional[DatabaseConfig] = None):
        self.config = config or db_config
        self._pool: Optional[Any] = None
        self._is_initialized = False

    @property
    def is_configured(self) -> bool:
        return self.config.is_external_configured()

    async def init_pool(self) -> bool:
        """
        Initializes the asyncpg connection pool if external database is configured.
        Returns True if pool was created successfully, False otherwise.
        """
        if not self.is_configured:
            logger.info("External database credentials not configured; skipping connection pool initialization.")
            return False

        if not HAS_ASYNCPG:
            logger.error("Cannot initialize external DB pool: 'asyncpg' library is missing.")
            return False

        if self._pool is not None:
            return True

        try:
            target_url = self.config.get_external_database_url()
            logger.info(
                f"Connecting to external database at {self.config.external_host}:{self.config.external_port}/{self.config.external_name}..."
            )
            # asyncpg accepts connection parameters or DSN
            if self.config.external_database_url:
                self._pool = await asyncpg.create_pool(
                    dsn=self.config.external_database_url,
                    min_size=self.config.external_min_connections,
                    max_size=self.config.external_max_connections,
                    command_timeout=self.config.external_timeout,
                )
            else:
                ssl_arg = None
                if self.config.external_sslmode in ["require", "verify-ca", "verify-full"]:
                    ssl_arg = True
                elif self.config.external_sslmode == "disable":
                    ssl_arg = False

                self._pool = await asyncpg.create_pool(
                    host=self.config.external_host,
                    port=self.config.external_port,
                    database=self.config.external_name,
                    user=self.config.external_user,
                    password=self.config.external_password,
                    ssl=ssl_arg,
                    min_size=self.config.external_min_connections,
                    max_size=self.config.external_max_connections,
                    command_timeout=self.config.external_timeout,
                )
            self._is_initialized = True
            logger.info("External database connection pool initialized successfully.")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to external database: {e}")
            self._pool = None
            return False

    async def close_pool(self):
        """
        Gracefully terminates all active connections in the connection pool.
        """
        if self._pool is not None:
            try:
                await self._pool.close()
                logger.info("External database connection pool closed.")
            except Exception as e:
                logger.warning(f"Error while closing external database connection pool: {e}")
            finally:
                self._pool = None
                self._is_initialized = False

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[Any, None]:
        """
        Acquires a connection from the pool as an asynchronous context manager.
        """
        if self._pool is None:
            # Try initializing pool if external DB is configured
            if self.is_configured:
                await self.init_pool()

        if self._pool is None:
            raise RuntimeError(
                "External database connection pool is not available. "
                "Check database credentials and network reachability."
            )

        async with self._pool.acquire() as conn:
            yield conn

    async def check_health(self) -> Dict[str, Any]:
        """
        Runs a health check ping against the external database.
        Returns a dictionary containing connection status, latency, and masked metadata.
        """
        if not self.is_configured:
            return {
                "configured": False,
                "status": "not_configured",
                "message": "External database credentials are not set in environment.",
                "database_type": self.config.external_db_type,
            }

        if not HAS_ASYNCPG:
            return {
                "configured": True,
                "status": "driver_missing",
                "message": "asyncpg driver is not installed.",
                "database_type": self.config.external_db_type,
            }

        start = time.perf_counter()
        try:
            # Try via pool first, or temporary direct connection
            if self._pool is not None:
                async with self._pool.acquire() as conn:
                    result = await conn.fetchval("SELECT 1")
            else:
                target_url = self.config.get_external_database_url()
                conn = await asyncio.wait_for(
                    asyncpg.connect(target_url),
                    timeout=min(self.config.external_timeout, 5.0)
                )
                try:
                    result = await conn.fetchval("SELECT 1")
                finally:
                    await conn.close()

            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            return {
                "configured": True,
                "status": "connected",
                "latency_ms": latency_ms,
                "database_type": self.config.external_db_type,
                "host": self.config.external_host,
                "port": self.config.external_port,
                "database": self.config.external_name,
            }
        except Exception as e:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            return {
                "configured": True,
                "status": "unreachable",
                "error": str(e),
                "latency_ms": latency_ms,
                "database_type": self.config.external_db_type,
                "host": self.config.external_host,
                "port": self.config.external_port,
                "database": self.config.external_name,
            }

    async def execute(self, query: str, *args: Any) -> Optional[str]:
        """
        Executes a command (INSERT, UPDATE, DELETE, CREATE, etc.) on external database.
        """
        async with self.get_connection() as conn:
            return await conn.execute(query, *args)

    async def fetch_one(self, query: str, *args: Any) -> Optional[Dict[str, Any]]:
        """
        Fetches a single row from external database as a dict.
        """
        async with self.get_connection() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def fetch_all(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        """
        Fetches all matching rows from external database as a list of dicts.
        """
        async with self.get_connection() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(row) for row in rows]


# Global external database manager instance
external_db = ExternalDatabaseManager(db_config)


async def init_external_db() -> bool:
    """Helper to initialize external DB pool at application startup."""
    return await external_db.init_pool()


async def close_external_db():
    """Helper to close external DB pool at application shutdown."""
    await external_db.close_pool()


async def check_external_db_health() -> Dict[str, Any]:
    """Helper to ping and test external DB."""
    return await external_db.check_health()
