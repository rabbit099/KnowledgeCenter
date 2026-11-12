# Web application image (FastAPI). Build context: repository root.
# Builder: same uv as development; runtime: plain Python slim, non-root.
FROM ghcr.io/astral-sh/uv:0.13.7-python3.12-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12.16-slim-trixie
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app \
    && mkdir -p /data/storage /data/tmp && chown -R app:app /data
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini /opt/app/alembic.ini
COPY alembic /opt/app/alembic
WORKDIR /opt/app
USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "baza_wiedzy.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
