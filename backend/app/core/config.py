from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    database_url: str = Field(default="postgresql+psycopg2://dataq:dataq@localhost:5432/dataq")
    redis_url: str = Field(default="redis://localhost:6379/0")

    applicationinsights_connection_string: str | None = None

    sample_failures_retention_days: int = 30


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
