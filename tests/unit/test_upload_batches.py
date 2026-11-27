"""Upload batches (ADR-032) and the consistency of the upload limits."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from baza_wiedzy.core.config import Settings
from baza_wiedzy.modules.documents.batches import HELD, BatchStore

SECRET = "x" * 40


def test_a_batch_is_only_readable_by_its_owner(tmp_path: Path) -> None:
    store = BatchStore(tmp_path)
    owner, other = uuid.uuid4(), uuid.uuid4()
    batch = store.new_id()
    store.save(batch, owner, [{"name": "a.txt", "status": HELD}])
    assert store.load(batch, owner) == [{"name": "a.txt", "status": HELD}]
    assert store.load(batch, other) is None
    with store.locked(batch, other) as found:
        assert found is None
    for bogus in ("../../etc/passwd", "", "zz", uuid.uuid4().hex):
        assert store.load(bogus, owner) is None


def test_decisions_are_saved_under_the_lock(tmp_path: Path) -> None:
    store = BatchStore(tmp_path)
    owner = uuid.uuid4()
    batch = store.new_id()
    store.save(batch, owner, [{"status": HELD}])
    with store.locked(batch, owner) as found:
        assert found is not None
        rows, save = found
        rows[0]["status"] = "skipped"
        save()
    assert store.load(batch, owner) == [{"status": "skipped"}]


def test_old_batches_and_held_files_are_removed(tmp_path: Path) -> None:
    store = BatchStore(tmp_path)
    owner = uuid.uuid4()
    old, new = store.new_id(), store.new_id()
    for batch in (old, new):
        store.save(batch, owner, [])
        store.held_path(batch, 0).write_bytes(b"held")
    past = time.time() - 2 * 3600
    for item in (tmp_path / ".batches").iterdir():
        if old in item.name:
            os.utime(item, (past, past))
    assert store.cleanup(3600) == 1
    assert store.load(old, owner) is None and not (tmp_path / ".batches" / old).exists()
    assert store.load(new, owner) == [] and store.held_path(new, 0).exists()


def test_total_upload_size_never_exceeds_what_the_proxy_lets_through() -> None:
    settings = Settings(secret_key=SECRET, upload_max_total_mb=500, max_request_mb=110)
    assert settings.upload_total_mb == 109  # an older .env (MAX_REQUEST_MB=51/110) still starts
    assert Settings(secret_key=SECRET, max_request_mb=501).upload_total_mb == 500
    with pytest.raises(ValidationError, match="MAX_REQUEST_MB"):
        Settings(secret_key=SECRET, max_upload_mb=50, max_request_mb=50)
    with pytest.raises(ValidationError, match="UPLOAD_MAX_TOTAL_MB"):
        Settings(secret_key=SECRET, max_upload_mb=50, upload_max_total_mb=10)
    with pytest.raises(ValidationError, match="UPLOAD_MAX_FILES"):
        Settings(secret_key=SECRET, upload_max_files=0)


def test_storage_cleanup_removes_expired_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from baza_wiedzy.cli import main

    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("UPLOAD_BATCH_HOURS", "24")
    store = BatchStore(tmp_path)
    owner = uuid.uuid4()
    old, new = store.new_id(), store.new_id()
    for batch in (old, new):
        store.save(batch, owner, [])
        store.held_path(batch, 0).write_bytes(b"held")
    past = time.time() - 25 * 3600
    for item in (tmp_path / ".batches").iterdir():
        if old in item.name:
            os.utime(item, (past, past))
    assert main(["storage-cleanup"]) == 0
    out = capsys.readouterr().out
    assert "Removed 1 expired upload batch(es)" in out and "UPLOAD_BATCH_HOURS=24" in out
    assert store.load(old, owner) is None and store.load(new, owner) == []
