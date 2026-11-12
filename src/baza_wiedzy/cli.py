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


def _read_new_password(from_stdin: bool) -> str:
    """Ask twice on the terminal, or read one line from stdin. Never from arguments."""
    if from_stdin:
        line = sys.stdin.readline()
        return line[:-1] if line.endswith("\n") else line
    import getpass

    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat the password: ")
    if first != second:
        raise _CliError("The passwords do not match; nothing was changed.")
    return first


class _CliError(Exception):
    pass


def _create_admin(args: argparse.Namespace) -> int:
    from baza_wiedzy.core.auth.accounts import ensure_local_admin
    from baza_wiedzy.core.auth.passwords import PasswordPolicyError, Passwords, check_policy
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.session import make_engine, make_session_factory

    settings = Settings()
    try:
        password = check_policy(
            _read_new_password(args.password_stdin),
            min_length=settings.local_password_min_length,
            max_length=settings.local_password_max_length,
        )
    except PasswordPolicyError as exc:
        raise _CliError(f"{exc} Nothing was changed.") from exc
    passwords = Passwords(
        max_concurrent=settings.argon2_max_concurrent,
        max_length=settings.local_password_max_length,
    )
    engine = make_engine(settings)
    try:
        with make_session_factory(engine).begin() as db:
            result = ensure_local_admin(db, args.username, password, passwords)
    finally:
        engine.dispose()
    if result.created:
        print(f"Created the local admin {result.user.username!r}.")
    else:
        print(
            f"Password reset for the local admin {result.user.username!r}; account active, "
            f"Admin role ensured, {result.revoked_sessions} session(s) revoked."
        )
    return 0


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

    create_admin = commands.add_parser(
        "create-admin",
        help="create the local break-glass admin, or reset its password (asks for the password)",
    )
    create_admin.add_argument("username", help="local user name, for example bw-admin")
    create_admin.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from standard input (one line) instead of asking twice",
    )
    create_admin.set_defaults(handler=_create_admin)

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
        from baza_wiedzy.core.auth.accounts import AccountError
        from baza_wiedzy.core.db.migrate import MigrationError
        from baza_wiedzy.core.registry import ModuleError

        if isinstance(exc, MigrationError | ModuleError | AccountError | _CliError):
            print(f"error: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
