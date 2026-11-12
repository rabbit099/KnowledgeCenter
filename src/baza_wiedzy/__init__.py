"""Baza Wiedzy: internal company knowledge base."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: `version` in pyproject.toml (checked against the release tag).
    __version__ = version("baza-wiedzy")
except PackageNotFoundError:  # running from a source tree without installing the package
    __version__ = "0.0.0+unknown"
