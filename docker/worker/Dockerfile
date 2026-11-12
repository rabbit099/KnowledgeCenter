# Background worker image (Dramatiq): parsing, OCR, DOC conversion. Build context: repository root.
FROM ghcr.io/astral-sh/uv:0.13.7-python3.12-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra worker --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra worker --no-editable

FROM python:3.12.16-slim-trixie
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# OCR (tesseract pol+eng, ghostscript for OCRmyPDF), DOC conversion (LibreOffice Writer, no GUI),
# file type detection (libmagic).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng \
        ghostscript libreoffice-writer-nogui libmagic1t64 fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app \
    && mkdir -p /data/storage /data/tmp && chown -R app:app /data
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini /opt/app/alembic.ini
COPY alembic /opt/app/alembic
WORKDIR /opt/app
USER app
# The Dramatiq actors arrive in Phase 0 stage B6.
CMD ["dramatiq", "baza_wiedzy.worker", "--processes", "1", "--threads", "1"]
