"""Argon2id passwords and the local password policy."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from baza_wiedzy.core.auth.passwords import PasswordPolicyError, Passwords, check_policy

PW = "correct horse battery staple"


@pytest.fixture(scope="module")
def passwords() -> Passwords:
    return Passwords(max_concurrent=3, max_length=256)


def test_hash_is_argon2id_and_verifies(passwords: Passwords) -> None:
    hashed = passwords.hash(PW)
    assert hashed.startswith("$argon2id$")
    assert PW not in hashed
    assert passwords.verify(hashed, PW)
    assert not passwords.verify(hashed, PW + "!")
    assert not passwords.needs_rehash(hashed)


def test_missing_or_broken_hash_never_verifies(passwords: Passwords) -> None:
    assert not passwords.verify(None, PW)
    assert not passwords.verify("not-a-hash", PW)


def test_unicode_is_normalized(passwords: Passwords) -> None:
    # "ﬁ" (U+FB01 ligature) and "fi" are the same password after NFKC normalization.
    hashed = passwords.hash("conﬁdential password")
    assert passwords.verify(hashed, "confidential password")


def test_oversized_input_is_rejected_without_hashing(passwords: Passwords) -> None:
    hashed = passwords.hash(PW)
    assert not passwords.verify(hashed, "x" * 10_000)


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short", "at least 14"),
        ("x" * 257, "at most 256"),
        (" leading space pw", "whitespace"),
        ("trailing space pw ", "whitespace"),
    ],
)
def test_policy_rejects(password: str, message: str) -> None:
    with pytest.raises(PasswordPolicyError, match=message):
        check_policy(password, min_length=14, max_length=256)


def test_policy_accepts_long_passphrase_without_composition_rules() -> None:
    assert check_policy("all lowercase words here", min_length=14, max_length=256)


def test_concurrent_hashes_are_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    passwords = Passwords(max_concurrent=2, max_length=256)
    running = 0
    peak = 0
    lock = threading.Lock()

    def slow_hash(_: str) -> str:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return "$argon2id$fake"

    monkeypatch.setattr(passwords, "_hasher", SimpleNamespace(hash=slow_hash))
    threads = [threading.Thread(target=passwords.hash, args=(PW,)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 2
