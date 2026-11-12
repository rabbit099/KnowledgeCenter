"""Command line entry point (`baza-wiedzy`)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from baza_wiedzy import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baza-wiedzy")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args(argv)
    return 0
