"""Application settings read from environment variables (pydantic-settings)."""

from __future__ import annotations

from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_MODULES = ("documents", "ingestion", "search")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Baza Wiedzy"
    # Image tag the operator runs; shown on /health and /ready.
    app_version: str = "0.0.0"
    # postgresql+psycopg://user:password@db:5432/name (built in docker-compose.yml).
    database_url: str = "postgresql+psycopg://baza_wiedzy@localhost:5432/baza_wiedzy"
    # Small fixed pool: every connection holds the Polish dictionary (~27 MB, ADR-016).
    # app: 5 + 5 overflow per process; worker: 1 + 0 per process (set in docker-compose.yml).
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # redis://:password@redis:6379/0 (built in docker-compose.yml).
    redis_url: str = "redis://localhost:6379/0"
    # Comma-separated in the environment: ENABLED_MODULES=documents,ingestion,search
    enabled_modules: Annotated[tuple[str, ...], NoDecode] = DEFAULT_MODULES

    @field_validator("enabled_modules", mode="before")
    @classmethod
    def _split_modules(cls, value: object) -> object:
        if isinstance(value, str):
            names: list[str] = []
            for part in value.split(","):
                name = part.strip().lower()
                # The core is always enabled; listing it is harmless.
                if name and name != "core" and name not in names:
                    names.append(name)
            return tuple(names)
        return value
