"""The web application never imports parser libraries: they exist only in the worker image
(extra `worker`, ADR-015). A fresh interpreter is used so other tests cannot pre-load them."""

from __future__ import annotations

import subprocess
import sys

WORKER_ONLY = ("docx", "lingua", "lxml", "ocrmypdf", "pypdfium2", "pikepdf")

SCRIPT = f"""
import sys
from baza_wiedzy.core.config import Settings
from baza_wiedzy.main import create_app
from fastapi.testclient import TestClient

app = create_app(
    Settings(_env_file=None, secret_key="x" * 40, storage_dir="/nonexistent"),
    setup_logging=False,
)
TestClient(app).get("/health")
loaded = sorted(name for name in {WORKER_ONLY!r} if name in sys.modules)
print(",".join(loaded))
"""


def test_web_application_does_not_load_worker_libraries() -> None:
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == ""
