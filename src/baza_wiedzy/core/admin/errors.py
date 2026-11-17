"""Errors shown to the admin. `message` is an English gettext id, translated in the template."""

from __future__ import annotations

from typing import Any


class AdminError(Exception):
    def __init__(self, message: str, **params: Any) -> None:
        super().__init__(message)
        self.message = message
        self.params = params
