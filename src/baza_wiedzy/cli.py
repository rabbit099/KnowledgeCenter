"""Command line entry point (`baza-wiedzy`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from baza_wiedzy import __version__


def _migrate(_: argparse.Namespace) -> int:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.migrate import upgrade
    from baza_wiedzy.core.registry import load_registry

    settings = Settings()
    registry = load_registry(settings.enabled_modules)
    targets = upgrade(settings, registry)
    print(f"Database schema is up to date ({', '.join(targets)}).")
    return 0


def _revision(args: argparse.Namespace) -> int:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.migrate import new_revision

    path = new_revision(Settings(), args.branch, args.message)
    print(f"Created {path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baza-wiedzy")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    migrate = commands.add_parser(
        "migrate", help="apply database migrations of the core and the enabled modules"
    )
    migrate.set_defaults(handler=_migrate)

    revision = commands.add_parser(
        "revision", help="create an empty migration in the branch of a module (development)"
    )
    revision.add_argument("--branch", required=True, help="core or a module name, e.g. search")
    revision.add_argument("-m", "--message", required=True, help="short description")
    revision.set_defaults(handler=_revision)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return int(args.handler(args))
    except Exception as exc:  # readable message for operators; details with --traceback later
        from baza_wiedzy.core.db.migrate import MigrationError
        from baza_wiedzy.core.registry import ModuleError

        if isinstance(exc, MigrationError | ModuleError):
            print(f"error: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
