import os
from typing import Optional, Dict, Any
from urllib.parse import quote_plus


class DatabaseConfig:
    """
    Configuration holder for both local SQLite and External Database connections.
    Loads settings from environment variables with sensible defaults.
    """

    def __init__(
        self,
        db_type: str = "sqlite",
        database_path: str = "baserun.db",
        external_db_type: str = "postgresql",
        external_host: str = "localhost",
        external_port: int = 5432,
        external_name: str = "baserun_db",
        external_user: str = "postgres",
        external_password: str = "",
        external_sslmode: str = "prefer",
        external_min_connections: int = 1,
        external_max_connections: int = 10,
        external_timeout: float = 10.0,
        external_database_url: Optional[str] = None,
    ):
        self.db_type = db_type
        self.database_path = database_path

        # External DB settings
        self.external_db_type = external_db_type
        self.external_host = external_host
        self.external_port = external_port
        self.external_name = external_name
        self.external_user = external_user
        self.external_password = external_password
        self.external_sslmode = external_sslmode
        self.external_min_connections = external_min_connections
        self.external_max_connections = external_max_connections
        self.external_timeout = external_timeout
        self.external_database_url = external_database_url

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """
        Loads database settings from environment variables.
        Supports both EXTERNAL_DB_* and standard DB_* variable prefixes.
        """
        db_type = os.getenv("DATABASE_TYPE", "sqlite").lower()
        is_serverless = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))
        default_db_path = "/tmp/baserun.db" if is_serverless else "baserun.db"
        database_path = os.getenv("DATABASE_PATH", default_db_path)

        external_db_type = (
            os.getenv("EXTERNAL_DB_TYPE")
            or os.getenv("DB_TYPE")
            or "postgresql"
        ).lower()

        external_host = (
            os.getenv("EXTERNAL_DB_HOST")
            or os.getenv("DB_HOST")
            or "localhost"
        )

        port_str = (
            os.getenv("EXTERNAL_DB_PORT")
            or os.getenv("DB_PORT")
            or "5432"
        )
        try:
            external_port = int(port_str)
        except ValueError:
            external_port = 5432

        external_name = (
            os.getenv("EXTERNAL_DB_NAME")
            or os.getenv("DB_NAME")
            or "baserun_db"
        )

        external_user = (
            os.getenv("EXTERNAL_DB_USER")
            or os.getenv("DB_USER")
            or "postgres"
        )

        external_password = (
            os.getenv("EXTERNAL_DB_PASSWORD")
            or os.getenv("DB_PASSWORD")
            or ""
        )

        external_sslmode = (
            os.getenv("EXTERNAL_DB_SSLMODE")
            or os.getenv("DB_SSLMODE")
            or "prefer"
        )

        min_conn_str = os.getenv("EXTERNAL_DB_MIN_CONNECTIONS", "1")
        try:
            external_min_connections = int(min_conn_str)
        except ValueError:
            external_min_connections = 1

        max_conn_str = os.getenv("EXTERNAL_DB_MAX_CONNECTIONS", "10")
        try:
            external_max_connections = int(max_conn_str)
        except ValueError:
            external_max_connections = 10

        timeout_str = os.getenv("EXTERNAL_DB_TIMEOUT", "10.0")
        try:
            external_timeout = float(timeout_str)
        except ValueError:
            external_timeout = 10.0

        external_database_url = (
            os.getenv("EXTERNAL_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )

        return cls(
            db_type=db_type,
            database_path=database_path,
            external_db_type=external_db_type,
            external_host=external_host,
            external_port=external_port,
            external_name=external_name,
            external_user=external_user,
            external_password=external_password,
            external_sslmode=external_sslmode,
            external_min_connections=external_min_connections,
            external_max_connections=external_max_connections,
            external_timeout=external_timeout,
            external_database_url=external_database_url,
        )

    def is_external_configured(self) -> bool:
        """
        Determines if an external database has been configured with valid credentials or URI.
        """
        if self.external_database_url:
            return True
        # If user explicitly provided password or changed host from default localhost with credentials
        if self.external_password and self.external_user:
            return True
        return False

    def get_external_database_url(self) -> str:
        """
        Returns full connection URI for external database.
        Constructs URL from discrete credentials if EXTERNAL_DATABASE_URL is not set.
        """
        if self.external_database_url:
            return self.external_database_url

        encoded_user = quote_plus(self.external_user)
        encoded_pass = quote_plus(self.external_password)
        auth = f"{encoded_user}:{encoded_pass}" if self.external_password else encoded_user

        query_params = []
        if self.external_sslmode:
            query_params.append(f"sslmode={self.external_sslmode}")

        query_string = f"?{'&'.join(query_params)}" if query_params else ""

        return (
            f"{self.external_db_type}://{auth}@"
            f"{self.external_host}:{self.external_port}/{self.external_name}{query_string}"
        )

    def to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """
        Returns a dictionary representation of configuration for logging or health checks.
        """
        pwd = "******" if (mask_secrets and self.external_password) else self.external_password
        masked_url = None
        if self.external_database_url:
            if mask_secrets and "@" in self.external_database_url:
                try:
                    prefix, rest = self.external_database_url.split("@", 1)
                    scheme, user_info = prefix.split("://", 1)
                    user = user_info.split(":")[0] if ":" in user_info else user_info
                    masked_url = f"{scheme}://{user}:******@{rest}"
                except Exception:
                    masked_url = "******"
            else:
                masked_url = self.external_database_url

        return {
            "db_type": self.db_type,
            "database_path": self.database_path,
            "external_configured": self.is_external_configured(),
            "external_db_type": self.external_db_type,
            "external_host": self.external_host,
            "external_port": self.external_port,
            "external_name": self.external_name,
            "external_user": self.external_user,
            "external_password": pwd,
            "external_sslmode": self.external_sslmode,
            "external_min_connections": self.external_min_connections,
            "external_max_connections": self.external_max_connections,
            "external_timeout": self.external_timeout,
            "external_database_url": masked_url,
        }


# Singleton configuration instance
db_config = DatabaseConfig.from_env()
