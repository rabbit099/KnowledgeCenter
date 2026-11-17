"""Application settings read from environment variables (pydantic-settings)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_MODULES = ("documents", "ingestion", "search")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Baza Wiedzy"
    # DEBUG, INFO, WARNING, ERROR. Logs are JSON lines (baza_wiedzy.core.logs).
    log_level: str = "INFO"
    # postgresql+psycopg://user:password@db:5432/name (built in docker-compose.yml).
    database_url: str = "postgresql+psycopg://baza_wiedzy@localhost:5432/baza_wiedzy"
    # Small fixed pool: every connection holds the Polish dictionary (~27 MB, ADR-016).
    # app: 5 + 5 overflow per process; worker: 1 + 0 per process (set in docker-compose.yml).
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Alembic configuration file; relative paths are resolved from the working directory
    # (/opt/app in the images, the repository root in development).
    alembic_ini: str = "alembic.ini"
    # redis://:password@redis:6379/0 (built in docker-compose.yml).
    redis_url: str = "redis://localhost:6379/0"
    # Signs CSRF tokens. The web application refuses to start with fewer than 32 characters.
    secret_key: str = ""
    # Sessions (ADR-007): idle limit and absolute lifetime.
    session_idle_minutes: int = 120
    session_absolute_hours: int = 12
    # Only for local experiments without TLS; production always uses Secure cookies.
    session_cookie_secure: bool = True
    # Failed logins (counted in Redis, fixed window). Locks expire by themselves.
    login_window_minutes: int = 15
    login_lockout_minutes: int = 15
    login_max_failures_account: int = 30
    login_max_failures_account_ip: int = 5
    login_max_failures_ip: int = 20
    # Active Directory (LDAPS only; certificate verification cannot be turned off).
    ldap_enabled: bool = False
    # One or more ldaps:// URIs, comma-separated; tried in order when one is unreachable.
    ldap_uri: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    # Alternative to LDAP_BIND_PASSWORD: path of a file holding it (Docker secrets).
    ldap_bind_password_file: str = ""
    ldap_base_dn: str = ""
    # sAMAccountName (log in as "jan") or userPrincipalName (log in as "jan@firma.local").
    ldap_login_attribute: str = "sAMAccountName"
    # UPN suffix added to bare names when LDAP_LOGIN_ATTRIBUTE=userPrincipalName.
    ldap_upn_suffix: str = ""
    # Extra filter AND-ed to the user search.
    ldap_user_filter: str = "(objectClass=user)"
    ldap_ca_file: str = ""
    ldap_nested_groups: bool = False
    ldap_connect_timeout_seconds: int = 5
    ldap_receive_timeout_seconds: int = 10
    ldap_page_size: int = 500
    # Documents: largest accepted file (the proxy's MAX_REQUEST_MB must be a little larger) and
    # the storage volume (originals, .incoming/ for uploads in progress).
    max_upload_mb: int = 50
    storage_dir: str = "/data/storage"
    # Ingestion (worker): attempts per version (transient errors only: database, Redis, time
    # limit), exponential delay between attempts, time limit of one attempt, and the age after
    # which a version still "processing" counts as interrupted (must exceed the time limit).
    ingest_max_attempts: int = 3
    ingest_retry_min_seconds: int = 30
    ingest_retry_max_seconds: int = 600
    ingest_time_limit_seconds: int = 900
    ingest_stale_minutes: int = 30
    # Extraction limits: nesting of e-mail attachments, parts per version, text per version.
    ingest_max_depth: int = 3
    ingest_max_parts: int = 2000
    ingest_max_text_mb: int = 50
    # Parts with fewer letters than this get language "unknown" instead of a guess.
    language_min_letters: int = 40
    # Delivered events are kept this long in ing_outbox (diagnostics), then removed.
    outbox_retention_days: int = 7
    # Virus scan step before extraction. Only "none" exists for now; the step is the place for
    # ClamAV (docs: ingestion-and-ocr.md, "Virus scan hook").
    scan_hook: Literal["none"] = "none"
    # Largest number of audit entries in one CSV export (the export says when it was cut).
    audit_export_max_rows: int = 10000
    # Local (break-glass) passwords: length limits in characters, and at most this many Argon2id
    # hashes computed at the same time per process (each one uses 64 MiB of memory).
    local_password_min_length: int = 14
    local_password_max_length: int = 256
    argon2_max_concurrent: int = 3
    # Each worker process writes a heartbeat key this often; the key expires after 4 intervals,
    # so /ready reports "no heartbeat" about a minute after the last worker stopped.
    worker_heartbeat_seconds: int = 15
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
