"""Application settings read from environment variables (pydantic-settings)."""

from __future__ import annotations

from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_MODULES = ("documents", "ingestion", "search")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Baza Wiedzy"
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
