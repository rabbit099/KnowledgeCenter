"""Command line entry point (`baza-wiedzy`)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from baza_wiedzy import __version__

if TYPE_CHECKING:
    from baza_wiedzy.core.runtime import Runtime


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


def _unlock_login(args: argparse.Namespace) -> int:
    import redis

    from baza_wiedzy.core.audit.service import CLI_ACTOR, record
    from baza_wiedzy.core.auth.accounts import find_user
    from baza_wiedzy.core.auth.ratelimit import Limits, LoginLimiter
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.session import make_engine, make_session_factory

    if not args.user and not args.ip:
        raise _CliError("Give --user and/or --ip.")
    settings = Settings()
    client = redis.Redis.from_url(settings.redis_url)
    limiter = LoginLimiter(client, Limits(0, 0, 0, 0, 0))  # thresholds are not used here
    removed = limiter.unlock(username=args.user, ip=args.ip)
    client.close()
    engine = make_engine(settings)
    try:
        with make_session_factory(engine).begin() as db:
            user = find_user(db, args.user) if args.user else None
            record(
                db,
                "cli.unlock_login",
                CLI_ACTOR,
                entity_type="user" if user else None,
                entity_id=user.id if user else None,
                details={
                    "known_user": user is not None,
                    "ip": args.ip,
                    "removed_keys": removed,
                },
            )
    finally:
        engine.dispose()
    print(f"Login limits cleared ({removed} counter/lock key(s) removed).")
    return 0


def _ad_check(args: argparse.Namespace) -> int:
    """Diagnostics for a real AD: configuration, service bind, user lookup, groups, mapping.

    Changes nothing. With --password it also tries the user bind (password asked, never shown).
    """
    from sqlalchemy import select

    from baza_wiedzy.core.auth.ldap import AdDirectory, AdUnavailable, LdapConfig
    from baza_wiedzy.core.auth.provisioning import mapped_role_ids
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.models import Role
    from baza_wiedzy.core.db.session import make_engine, make_session_factory

    settings = Settings()
    config = LdapConfig.from_settings(settings)
    directory = AdDirectory(config)
    print(f"LDAP_ENABLED={settings.ldap_enabled}  URIs: {', '.join(config.uris)}")
    print(
        f"Base DN: {config.base_dn}  login attribute: {config.login_attribute}  "
        f"nested groups: {config.nested_groups}"
    )
    try:
        if not args.user:
            directory.find("\x00-no-such-user")  # service bind and one search
            print("Service account bind: OK")
            return 0
        login = directory.normalize(args.user)
        print(f"Normalized login: {login}")
        found = directory.find(login)
        print("Service account bind: OK")
        if found is None:
            print("User: NOT FOUND (check LDAP_BASE_DN, LDAP_USER_FILTER, LDAP_LOGIN_ATTRIBUTE)")
            return 1
        entry, groups = found
        attrs = entry["attributes"]
        disabled = directory._disabled(attrs)
        print(f"User DN: {entry['dn']}")
        print(f"Display name: {attrs.get('displayName')}  disabled in AD: {disabled}")
        print(f"Groups ({len(groups)}):")
        for group in groups:
            print(f"  {group}")
        engine = make_engine(settings)
        try:
            with make_session_factory(engine)() as db:
                role_ids = mapped_role_ids(db, groups)
                names = sorted(db.scalars(select(Role.name).where(Role.id.in_(role_ids))))
        finally:
            engine.dispose()
        print(f"Roles from the group mapping: {', '.join(names) or '(none)'}")
        if args.password:
            import getpass

            ok = directory.authenticate(login, getpass.getpass("AD password: ")) is not None
            print(f"User bind with the password: {'OK' if ok else 'REFUSED'}")
            return 0 if ok else 1
        return 0
    except AdUnavailable as exc:
        print(f"Active Directory unavailable: {exc.reason} (details in the error log above)")
        return 2


def _user_active(args: argparse.Namespace) -> int:
    from baza_wiedzy.core.admin.errors import AdminError
    from baza_wiedzy.core.admin.users import set_active
    from baza_wiedzy.core.audit.service import CLI_ACTOR
    from baza_wiedzy.core.auth.accounts import find_user
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.session import make_engine, make_session_factory

    engine = make_engine(Settings())
    try:
        with make_session_factory(engine).begin() as db:
            user = find_user(db, args.username)
            if user is None:
                raise _CliError(f"No user named {args.username!r}.")
            try:
                revoked = set_active(db, CLI_ACTOR, user, args.state == "on", ip=None)
            except AdminError as exc:
                raise _CliError(exc.message) from exc
            name = user.username
    finally:
        engine.dispose()
    state = "active" if args.state == "on" else f"inactive, {revoked} session(s) revoked"
    print(f"User {name!r} is now {state}.")
    return 0


def _storage_cleanup(args: argparse.Namespace) -> int:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.modules.documents.storage import LocalStorage

    settings = Settings()
    removed = LocalStorage(settings.storage_dir).cleanup_incoming(int(args.older_than * 3600))
    print(f"Removed {removed} orphaned incoming file(s) from {settings.storage_dir}/.incoming.")
    return 0


def _cli_runtime() -> Runtime:
    from baza_wiedzy.core import runtime
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.queue import setup_broker
    from baza_wiedzy.core.registry import load_registry

    settings = Settings()
    registry = load_registry(settings.enabled_modules)
    if "ingestion" not in registry:
        raise _CliError("The ingestion module is not enabled (ENABLED_MODULES).")
    setup_broker(settings, registry)
    return runtime.build(settings, registry)


def _reprocess(args: argparse.Namespace) -> int:
    import uuid

    from baza_wiedzy.core.audit.service import CLI_ACTOR
    from baza_wiedzy.modules.documents.services import api as documents
    from baza_wiedzy.modules.ingestion import startup
    from baza_wiedzy.modules.ingestion.services import api
    from baza_wiedzy.modules.ingestion.tasks import enqueue

    if not (args.ids or args.failed or args.stuck):
        raise _CliError("Give document or version IDs, --failed or --stuck.")
    rt = _cli_runtime()
    try:
        if args.stuck:
            result = startup.recover(rt)
            print(
                f"Stuck versions: {result.requeued_stuck} queued again, {result.failed_stuck} "
                f"failed (no attempts left); waiting versions queued again: "
                f"{result.requeued_pending}."
            )
        targets: list[uuid.UUID] = []
        with rt.session_factory() as db:
            if args.failed:
                targets.extend(documents.ids_with_status(db, documents.FAILED))
            for raw in args.ids:
                try:
                    key = uuid.UUID(raw)
                except ValueError as exc:
                    raise _CliError(f"Not an ID: {raw!r}.") from exc
                if db.get(documents.Version, key) is not None:
                    targets.append(key)
                    continue
                document = db.get(documents.Document, key)
                if document is None or document.latest_version_id is None:
                    raise _CliError(f"No document or version with ID {raw}.")
                targets.append(document.latest_version_id)
        queued = busy = 0
        for version_id in dict.fromkeys(targets):
            try:
                with rt.session_factory.begin() as db:
                    api.request_reprocess(db, rt.settings, version_id, actor=CLI_ACTOR, ip=None)
            except api.ReprocessBusy:
                busy += 1
                print(f"  {version_id}: being processed right now, skipped")
                continue
            enqueue(version_id, rt.settings)
            queued += 1
        if targets:
            print(f"Queued for processing again: {queued}; skipped (busy): {busy}.")
    finally:
        rt.close()
    return 0


def _events_redeliver(_: argparse.Namespace) -> int:
    from baza_wiedzy.modules.ingestion import outbox

    rt = _cli_runtime()
    try:
        delivery = outbox.deliver(rt.session_factory, rt.events)
        pruned = outbox.prune(rt.session_factory, older_than_days=rt.settings.outbox_retention_days)
        with rt.session_factory() as db:
            left = outbox.undelivered(db)
    finally:
        rt.close()
    print(
        f"Events delivered: {delivery.delivered}, failed: {delivery.failed}, still undelivered: "
        f"{left}; old delivered entries removed: {pruned}."
    )
    return 0 if delivery.failed == 0 else 1


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

    unlock = commands.add_parser(
        "unlock-login", help="clear failed-login counters and locks for a user name and/or address"
    )
    unlock.add_argument("--user", help="user name (all addresses)")
    unlock.add_argument("--ip", help="client address (all user names)")
    unlock.set_defaults(handler=_unlock_login)

    ad_check = commands.add_parser(
        "ad-check", help="test the Active Directory configuration (read-only diagnostics)"
    )
    ad_check.add_argument("--user", help="AD login to look up, e.g. jan or FIRMA\\jan")
    ad_check.add_argument(
        "--password", action="store_true", help="also test the user's password (asked)"
    )
    ad_check.set_defaults(handler=_ad_check)

    user_active = commands.add_parser(
        "user-active", help="activate or deactivate a user (the last active admin is protected)"
    )
    user_active.add_argument("username")
    user_active.add_argument("state", choices=["on", "off"])
    user_active.set_defaults(handler=_user_active)

    cleanup = commands.add_parser(
        "storage-cleanup", help="remove orphaned files of interrupted uploads from .incoming/"
    )
    cleanup.add_argument(
        "--older-than", type=float, default=1.0, metavar="HOURS", help="age limit (default 1)"
    )
    cleanup.set_defaults(handler=_storage_cleanup)

    reprocess = commands.add_parser(
        "reprocess",
        help="process documents again (audited), or recover versions stuck in processing",
    )
    reprocess.add_argument(
        "ids", nargs="*", metavar="ID", help="document ID (its latest version) or version ID"
    )
    reprocess.add_argument("--failed", action="store_true", help="every version that failed")
    reprocess.add_argument(
        "--stuck",
        action="store_true",
        help="versions processing longer than INGEST_STALE_MINUTES, and waiting ones (no audit)",
    )
    reprocess.set_defaults(handler=_reprocess)

    events = commands.add_parser("events", help="event outbox of the ingestion module")
    events_commands = events.add_subparsers(dest="events_command", metavar="ACTION", required=True)
    redeliver = events_commands.add_parser(
        "redeliver", help="deliver events that were not delivered yet; remove old delivered ones"
    )
    redeliver.set_defaults(handler=_events_redeliver)

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
        from baza_wiedzy.core.auth.ldap import LdapConfigError
        from baza_wiedzy.core.db.migrate import MigrationError
        from baza_wiedzy.core.registry import ModuleError

        if isinstance(
            exc, MigrationError | ModuleError | AccountError | _CliError | LdapConfigError
        ):
            print(f"error: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
