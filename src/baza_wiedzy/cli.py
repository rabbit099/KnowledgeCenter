"""Command line entry point (`baza-wiedzy`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from baza_wiedzy import __version__


def _migrate(_: argparse.Namespace) -> int:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.migrate import upgrade

    heads = upgrade(Settings())
    print(f"Database schema is up to date (heads: {', '.join(heads)}).")
    return 0


def _revision(args: argparse.Namespace) -> int:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.migrate import new_revision

    path = new_revision(Settings(), args.branch, args.message)
    print(f"Created {path}")
    return 0


def _worker_ping(args: argparse.Namespace) -> int:
    import time
    import uuid

    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.queue import setup_broker
    from baza_wiedzy.core.registry import load_registry
    from baza_wiedzy.core.tasks import PING_PREFIX, ping

    settings = Settings()
    broker = setup_broker(settings, load_registry(settings.enabled_modules))
    token = uuid.uuid4().hex
    started = time.monotonic()
    ping.send(token)
    while time.monotonic() - started < args.timeout:
        if broker.client.get(f"{PING_PREFIX}{token}") is not None:
            print(f"Worker answered in {time.monotonic() - started:.2f} s.")
            return 0
        time.sleep(0.2)
    print(f"No answer from a worker within {args.timeout} s.", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baza-wiedzy")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    migrate = commands.add_parser(
        "migrate", help="apply all database migrations (core and every module)"
    )
    migrate.set_defaults(handler=_migrate)

    revision = commands.add_parser(
        "revision", help="create an empty migration in the branch of a module (development)"
    )
    revision.add_argument("--branch", required=True, help="core or a module name, e.g. search")
    revision.add_argument("-m", "--message", required=True, help="short description")
    revision.set_defaults(handler=_revision)

    worker_ping = commands.add_parser(
        "worker-ping", help="send a test job and wait until a worker has processed it"
    )
    worker_ping.add_argument("--timeout", type=float, default=10.0, help="seconds to wait")
    worker_ping.set_defaults(handler=_worker_ping)

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
