"""Local passwords: Argon2id (argon2-cffi defaults, RFC 9106 low-memory profile).

Only the break-glass admin has a local password. The number of hashes computed at the same time
is limited per process (each uses 64 MiB), so a burst of login attempts cannot exhaust memory.
"""

from __future__ import annotations

import threading
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class PasswordPolicyError(ValueError):
    """The new password does not meet the policy; the message is safe to show."""


def normalize(password: str) -> str:
    # The same characters typed on different keyboards/OSes must give the same password.
    return unicodedata.normalize("NFKC", password)


def check_policy(password: str, *, min_length: int, max_length: int) -> str:
    """Return the normalized password or raise PasswordPolicyError. No composition rules (NIST)."""
    normalized = normalize(password)
    if len(normalized) < min_length:
        raise PasswordPolicyError(f"The password must have at least {min_length} characters.")
    if len(normalized) > max_length:
        raise PasswordPolicyError(f"The password must have at most {max_length} characters.")
    if normalized.strip() != normalized or not normalized.strip():
        raise PasswordPolicyError("The password must not start or end with whitespace.")
    return normalized


class Passwords:
    def __init__(self, *, max_concurrent: int, max_length: int) -> None:
        self._hasher = PasswordHasher()
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._max_length = max_length
        # Used to spend the same time on unknown user names as on known ones.
        self._dummy_hash = self._hasher.hash("dummy password for timing equalisation")

    def hash(self, password: str) -> str:
        with self._slots:
            return self._hasher.hash(normalize(password))

    def verify(self, password_hash: str | None, password: str) -> bool:
        """Constant-ish time check; a missing hash still costs one Argon2 verification."""
        candidate = normalize(password)
        if len(candidate) > self._max_length:
            # Do not spend Argon2 time on oversized input; it can never be a valid password.
            return False
        with self._slots:
            try:
                return self._hasher.verify(password_hash or self._dummy_hash, candidate) and (
                    password_hash is not None
                )
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
