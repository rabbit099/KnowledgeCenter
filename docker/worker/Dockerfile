# Background worker image (Dramatiq): parsing, OCR, DOC conversion. Build context: repository root.
FROM ghcr.io/astral-sh/uv:0.13.8-python3.12-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra worker --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra worker --no-editable

# Shared base of the released image and of the test image.
FROM python:3.12.16-slim-trixie AS base
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# OCR (Tesseract with the Debian language data pol+eng, the "fast" models; OCRmyPDF rasterises
# with pypdfium and writes text only, so Ghostscript (AGPL) is not installed, ADR-031), DOC
# conversion (LibreOffice Writer, no GUI), fonts for LibreOffice. Type detection needs no system
# library (ADR-021).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng \
        libreoffice-writer-nogui fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app \
    && mkdir -p /data/storage /data/tmp && chown -R app:app /data

# Test image for `make test-db` and CI (target `test`): the same system tools (LibreOffice,
# Tesseract) plus uv; the project and its dev dependencies are installed by the test command
# into a volume. Never released: the release and smoke builds use the default (last) stage.
FROM base AS test
COPY --from=ghcr.io/astral-sh/uv:0.13.8-python3.12-trixie-slim /usr/local/bin/uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never

# Released image (default target: the last stage).
FROM base AS runtime
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini /opt/app/alembic.ini
COPY alembic /opt/app/alembic
WORKDIR /opt/app
USER app
CMD ["dramatiq", "baza_wiedzy.worker", "--processes", "1", "--threads", "1"]
