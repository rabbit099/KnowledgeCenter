"""Liveness (/health) and readiness (/ready) checks.

/health answers as long as the process runs. /ready checks every dependency and returns 503
if one of them fails; operators and `update.sh` use it. Checks are plain callables stored on
`app.state.ready_checks`, so further stages add theirs (schema version, worker heartbeat).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import redis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    detail: str = "ok"


ReadyCheck = Callable[[], CheckResult]


def database_check(engine: Engine) -> ReadyCheck:
    def check() -> CheckResult:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            log.exception("readiness: database check failed")
            return CheckResult(False, "unreachable")
        return CheckResult(True)

    return check


def redis_check(client: redis.Redis) -> ReadyCheck:
    def check() -> CheckResult:
        try:
            client.ping()
        except redis.AuthenticationError:
            log.exception("readiness: redis authentication failed")
            return CheckResult(False, "authentication failed")
        except Exception:
            log.exception("readiness: redis check failed")
            return CheckResult(False, "unreachable")
        return CheckResult(True)

    return check


def run_checks(checks: dict[str, ReadyCheck]) -> tuple[bool, dict[str, CheckResult]]:
    results = {name: check() for name, check in checks.items()}
    return all(r.ok for r in results.values()), results


router = APIRouter()


@router.get("/health", include_in_schema=False)
def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": request.app.state.settings.app_version})


@router.get("/ready", include_in_schema=False)
def ready(request: Request) -> JSONResponse:
    ok, results = run_checks(request.app.state.ready_checks)
    body = {
        "status": "ok" if ok else "fail",
        "version": request.app.state.settings.app_version,
        "checks": {name: r.detail for name, r in results.items()},
    }
    return JSONResponse(body, status_code=200 if ok else 503)
