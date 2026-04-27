"""
Application configuration from environment variables.
Falls back to SQLite + in-memory dict when Postgres/Redis aren't available.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://smartroute:smartroute@localhost:5432/smartroute"
    SYNC_DATABASE_URL: str = "postgresql+psycopg2://smartroute:smartroute@localhost:5432/smartroute"
    USE_SQLITE: bool = False

    # ── Redis ─────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    USE_MEMORY_CACHE: bool = False

    # ── Kafka ─────────────────────────────────────────────────────────────
    KAFKA_BROKER: Optional[str] = None
    KAFKA_GPS_TOPIC: str = "telemetry.gps"

    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "SmartRoute Supply Chain API"
    DEBUG: bool = True
    SEED_ON_STARTUP: bool = True

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
