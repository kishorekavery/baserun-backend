import os
from typing import Optional, Dict, Any
from urllib.parse import quote_plus, unquote, urlsplit


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class DatabaseConfig:
    """
    PostgreSQL connection settings (Supabase, Neon, RDS, local Postgres, ...).
    Either DATABASE_URL / EXTERNAL_DATABASE_URL or the discrete
    EXTERNAL_DB_* (or DB_*) variables are used.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        name: str = "baserun_db",
        user: str = "postgres",
        password: str = "",
        sslmode: str = "prefer",
        min_connections: int = 1,
        max_connections: int = 10,
        timeout: float = 10.0,
        database_url: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.name = name
        self.user = user
        self.password = password
        self.sslmode = sslmode
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.timeout = timeout
        self.database_url = database_url

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        try:
            port = int(_first_env("EXTERNAL_DB_PORT", "DB_PORT", default="5432"))
        except ValueError:
            port = 5432

        return cls(
            host=_first_env("EXTERNAL_DB_HOST", "DB_HOST", default="localhost"),
            port=port,
            name=_first_env("EXTERNAL_DB_NAME", "DB_NAME", default="baserun_db"),
            user=_first_env("EXTERNAL_DB_USER", "DB_USER", default="postgres"),
            password=_first_env("EXTERNAL_DB_PASSWORD", "DB_PASSWORD"),
            sslmode=_first_env("EXTERNAL_DB_SSLMODE", "DB_SSLMODE", default="prefer"),
            min_connections=_int_env("EXTERNAL_DB_MIN_CONNECTIONS", 1),
            max_connections=_int_env("EXTERNAL_DB_MAX_CONNECTIONS", 10),
            timeout=_float_env("EXTERNAL_DB_TIMEOUT", 10.0),
            database_url=_first_env("DATABASE_URL", "EXTERNAL_DATABASE_URL") or None,
        )

    def get_database_url(self) -> str:
        """
        Returns the full connection URI, building it from discrete credentials
        when no DATABASE_URL is set.
        """
        if self.database_url:
            # asyncpg only understands the postgresql:// / postgres:// schemes
            if self.database_url.startswith("postgresql+asyncpg://"):
                return "postgresql://" + self.database_url[len("postgresql+asyncpg://"):]
            return self.database_url

        auth = quote_plus(self.user)
        if self.password:
            auth = f"{auth}:{quote_plus(self.password)}"
        query = f"?sslmode={self.sslmode}" if self.sslmode else ""
        return f"postgresql://{auth}@{self.host}:{self.port}/{self.name}{query}"

    def to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """
        Dictionary representation for logging or health checks.
        """
        masked_url = None
        if self.database_url:
            masked_url = self.database_url
            if mask_secrets and "@" in self.database_url:
                try:
                    prefix, rest = self.database_url.split("@", 1)
                    scheme, user_info = prefix.split("://", 1)
                    masked_url = f"{scheme}://{user_info.split(':')[0]}:******@{rest}"
                except ValueError:
                    masked_url = "******"

        host, port, name, user = self.host, self.port, self.name, self.user
        if self.database_url:
            try:
                parts = urlsplit(self.database_url)
                host, port = parts.hostname or host, parts.port or port
                name, user = parts.path.lstrip("/") or name, unquote(parts.username or user)
            except ValueError:
                pass

        return {
            "db_type": "postgresql",
            "host": host,
            "port": port,
            "name": name,
            "user": user,
            "password": "******" if (mask_secrets and self.password) else self.password,
            "sslmode": self.sslmode,
            "min_connections": self.min_connections,
            "max_connections": self.max_connections,
            "timeout": self.timeout,
            "database_url": masked_url,
        }


# Singleton configuration instance
db_config = DatabaseConfig.from_env()
