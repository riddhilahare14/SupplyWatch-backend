# """
# Application configuration from environment variables.
# Falls back to SQLite + in-memory dict when Postgres/Redis aren't available.
# """

# from pydantic_settings import BaseSettings
# from typing import Optional


# class Settings(BaseSettings):
#     # ── Database ──────────────────────────────────────────────────────────
#     DATABASE_URL: str = "postgresql+asyncpg://smartroute:smartroute@localhost:5432/smartroute"
#     SYNC_DATABASE_URL: str = "postgresql+psycopg2://smartroute:smartroute@localhost:5432/smartroute"
#     USE_SQLITE: bool = False

#     # ── Redis ─────────────────────────────────────────────────────────────
#     REDIS_URL: str = "redis://localhost:6379/0"
#     USE_MEMORY_CACHE: bool = False

#     # ── Kafka ─────────────────────────────────────────────────────────────
#     KAFKA_BROKER: Optional[str] = None
#     KAFKA_GPS_TOPIC: str = "telemetry.gps"

#     # ── App ───────────────────────────────────────────────────────────────
#     APP_NAME: str = "SmartRoute Supply Chain API"
#     DEBUG: bool = True
#     SEED_ON_STARTUP: bool = True

#     # ── Decision engine thresholds ────────────────────────────────────────
#     RISK_THRESHOLD: float = 0.70
#     AUTO_REROUTE_MAX_DETOUR_PCT: float = 15.0
#     AUTO_REROUTE_MAX_COST_PCT: float = 10.0

#     @property
#     def effective_database_url(self) -> str:
#         if self.USE_SQLITE:
#             return "sqlite+aiosqlite:///./smartroute.db"
#         return self.DATABASE_URL

#     @property
#     def effective_sync_database_url(self) -> str:
#         if self.USE_SQLITE:
#             return "sqlite:///./smartroute.db"
#         return self.SYNC_DATABASE_URL

#     model_config = {"env_file": ".env", "case_sensitive": True}


# settings = Settings()



"""
Application configuration from environment variables.
Falls back to SQLite + in-memory dict when Postgres/Redis aren't available.
"""

from pydantic_settings import BaseSettings
from typing import Optional, List


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://smartroute:smartroute@localhost:5432/smartroute"
    SYNC_DATABASE_URL: str = "postgresql+psycopg2://smartroute:smartroute@localhost:5432/smartroute"
    USE_SQLITE: bool = False

    # ── Database pool (ignored for SQLite) ────────────────────────────────
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_PRE_PING: bool = True

    # ── Redis ─────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    USE_MEMORY_CACHE: bool = False
    CACHE_TTL: int = 3600              # seconds — default TTL for all cached keys
    GPS_PING_BUFFER_SIZE: int = 60     # rolling window of GPS pings kept per shipment

    # ── Kafka ─────────────────────────────────────────────────────────────
    KAFKA_BROKER: Optional[str] = None
    KAFKA_GPS_TOPIC: str = "telemetry.gps"

    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "SmartRoute Supply Chain API"
    DEBUG: bool = True
    SEED_ON_STARTUP: bool = True

    # ── CORS ──────────────────────────────────────────────────────────────
    # Comma-separated in .env:  ALLOWED_ORIGINS=http://localhost:3000,https://myapp.com
    # "*" means allow all (fine for local dev, lock down in production).
    ALLOWED_ORIGINS: List[str] = ["*"]

    # ── Keep-alive (Render free-tier ping) ───────────────────────────────
    SELF_URL: str = "https://supplywatch-backend.onrender.com/"
    KEEPALIVE_INTERVAL: int = 30       # seconds between pings
    KEEPALIVE_ENABLED: bool = True     # set False in local dev to avoid noisy logs

    # ── Decision engine thresholds ────────────────────────────────────────
    RISK_THRESHOLD: float = 0.70
    AUTO_REROUTE_MAX_DETOUR_PCT: float = 15.0
    AUTO_REROUTE_MAX_COST_PCT: float = 10.0

    @property
    def effective_database_url(self) -> str:
        if self.USE_SQLITE:
            return "sqlite+aiosqlite:///./smartroute.db"
        return self.DATABASE_URL

    @property
    def effective_sync_database_url(self) -> str:
        if self.USE_SQLITE:
            return "sqlite:///./smartroute.db"
        return self.SYNC_DATABASE_URL

    model_config = {"env_file": ".env", "case_sensitive": True}


settings = Settings()