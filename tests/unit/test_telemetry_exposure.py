"""The application does not parse the traceparent header (no OpenTelemetry configured).

FastAPI 0.162 ships native OpenTelemetry support that extracts `traceparent` from requests only
when a tracer/meter/logger provider is configured. We configure none, so OpenTelemetry
advisories in header parsing (e.g. Dependabot alert #1, opentelemetry-api <= 1.45.1) are not
reachable. If this test fails, telemetry became active: re-assess such advisories (ADR-015).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from baza_wiedzy.core.config import Settings
from baza_wiedzy.main import create_app


def test_traceparent_is_never_extracted(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry import propagate  # the module FastAPI's telemetry calls

    calls: list[Any] = []

    def spy(*args: Any, **kwargs: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(propagate, "extract", spy)
    settings = Settings(_env_file=None, secret_key="telemetry-test-secret-0123456789abcdef")  # type: ignore[call-arg]
    app = create_app(settings, setup_logging=False)
    assert app._native_telemetry.enabled() is False
    header = {"traceparent": "00-" + "a" * 20_000 + "-" + "b" * 16 + "-01"}
    with TestClient(app, base_url="https://testserver") as client:
        for path in ("/health", "/login", "/"):
            client.get(path, headers=header, follow_redirects=False)
    assert calls == []


def test_the_spy_sees_extraction_when_tracing_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the test above: with an active (non no-op) tracer provider FastAPI does extract."""
    from fastapi import FastAPI
    from opentelemetry import propagate, trace

    calls: list[Any] = []
    original = propagate.extract

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(propagate, "extract", spy)

    class Provider(trace.TracerProvider):
        def get_tracer(self, *args: Any, **kwargs: Any) -> trace.Tracer:
            return trace.NoOpTracer()

    app = FastAPI(telemetry={"tracer_provider": Provider(), "metrics": False, "logs": False})

    @app.get("/x")
    def endpoint() -> dict[str, str]:
        return {}

    with TestClient(app) as client:
        client.get("/x", headers={"traceparent": "00-abc"})
    assert len(calls) == 1
