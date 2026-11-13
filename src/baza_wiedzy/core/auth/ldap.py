"""Active Directory over LDAPS (ADR-008, ADR-019).

Flow: service-account bind -> find the user by the login attribute (escaped) -> reject disabled
accounts -> bind as the user with the typed password -> read the user's groups with a paged
search (optionally nested, LDAP_MATCHING_RULE_IN_CHAIN). Certificate verification is always on,
referrals are not followed, connect and receive timeouts are explicit.

`AdUnavailable` means the directory could not be asked (network, TLS, service account): the
login page shows "temporarily unavailable" and the local break-glass admin keeps working.
"""

from __future__ import annotations

import logging
import ssl
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ldap3 import NONE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPCommunicationError, LDAPException, LDAPSocketOpenError
from ldap3.utils.conv import escape_filter_chars

from baza_wiedzy.core.config import Settings

log = logging.getLogger(__name__)

LOGIN_ATTRIBUTES = ("sAMAccountName", "userPrincipalName")
UAC_ACCOUNTDISABLE = 0x2
MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"
USER_ATTRIBUTES = [
    "objectGUID",
    "distinguishedName",
    "displayName",
    "mail",
    "userAccountControl",
    "sAMAccountName",
    "userPrincipalName",
]


class LdapConfigError(Exception):
    """Wrong LDAP_* configuration; the application refuses to start (readable message)."""


class AdUnavailable(Exception):
    """The directory could not be asked. `reason`: unreachable, tls, service_bind, error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AdUser:
    object_guid: uuid.UUID
    dn: str
    login: str  # normalized login value used as the application user name
    display_name: str | None
    email: str | None
    groups: tuple[str, ...]  # group DNs


# (bind_dn, password) -> unbound Connection. Tests pass a factory that builds MOCK_SYNC
# connections; production builds LDAPS connections from the configuration.
ConnectionFactory = Callable[[str, str], list[Connection]]


@dataclass(frozen=True)
class LdapConfig:
    uris: tuple[str, ...]
    bind_dn: str
    bind_password: str
    base_dn: str
    login_attribute: str
    upn_suffix: str
    user_filter: str
    ca_file: str
    nested_groups: bool
    connect_timeout: int
    receive_timeout: int
    page_size: int

    @classmethod
    def from_settings(cls, settings: Settings) -> LdapConfig:
        problems: list[str] = []
        uris = tuple(u.strip() for u in settings.ldap_uri.split(",") if u.strip())
        if not uris:
            problems.append("LDAP_URI is empty")
        problems += [
            f"LDAP_URI {u!r} must start with ldaps://"
            for u in uris
            if not u.lower().startswith("ldaps://")
        ]
        password = settings.ldap_bind_password
        if settings.ldap_bind_password_file:
            path = Path(settings.ldap_bind_password_file)
            if not path.is_file():
                problems.append(f"LDAP_BIND_PASSWORD_FILE {path} does not exist")
            else:
                password = path.read_text(encoding="utf-8").rstrip("\r\n")
        for name, value in (
            ("LDAP_BIND_DN", settings.ldap_bind_dn),
            ("LDAP_BIND_PASSWORD (or LDAP_BIND_PASSWORD_FILE)", password),
            ("LDAP_BASE_DN", settings.ldap_base_dn),
        ):
            if not value.strip():
                problems.append(f"{name} is empty")
        if settings.ldap_login_attribute not in LOGIN_ATTRIBUTES:
            problems.append(f"LDAP_LOGIN_ATTRIBUTE must be one of {', '.join(LOGIN_ATTRIBUTES)}")
        if settings.ldap_login_attribute == "userPrincipalName" and not settings.ldap_upn_suffix:
            problems.append(
                "LDAP_UPN_SUFFIX is required with LDAP_LOGIN_ATTRIBUTE=userPrincipalName"
            )
        if not settings.ldap_ca_file or not Path(settings.ldap_ca_file).is_file():
            problems.append(
                f"LDAP_CA_FILE {settings.ldap_ca_file!r} does not exist (PEM of the CA that "
                "signed the domain controllers' certificates)"
            )
        if problems:
            raise LdapConfigError("Active Directory configuration: " + "; ".join(problems) + ".")
        return cls(
            uris=uris,
            bind_dn=settings.ldap_bind_dn,
            bind_password=password,
            base_dn=settings.ldap_base_dn,
            login_attribute=settings.ldap_login_attribute,
            upn_suffix=settings.ldap_upn_suffix.lstrip("@").lower(),
            user_filter=settings.ldap_user_filter,
            ca_file=settings.ldap_ca_file,
            nested_groups=settings.ldap_nested_groups,
            connect_timeout=settings.ldap_connect_timeout_seconds,
            receive_timeout=settings.ldap_receive_timeout_seconds,
            page_size=settings.ldap_page_size,
        )


def normalize_login(raw: str, login_attribute: str, upn_suffix: str) -> str:
    """One canonical, lower-case form per person, whatever the user typed.

    sAMAccountName: "FIRMA\\Jan", "jan@firma.local", "JAN" -> "jan".
    userPrincipalName: "FIRMA\\Jan", "jan", "Jan@Firma.Local" -> "jan@firma.local"
    (a bare name gets LDAP_UPN_SUFFIX).
    """
    name = raw.strip()
    if "\\" in name:  # DOMAIN\name (down-level logon name)
        name = name.split("\\", 1)[1]
    name = name.lower()
    if login_attribute == "sAMAccountName":
        return name.split("@", 1)[0]
    return name if "@" in name else f"{name}@{upn_suffix}"


def ldaps_factory(config: LdapConfig) -> ConnectionFactory:
    tls = Tls(
        validate=ssl.CERT_REQUIRED,  # never CERT_NONE
        version=ssl.PROTOCOL_TLS_CLIENT,
        ssl_options=[ssl.OP_NO_TLSv1, ssl.OP_NO_TLSv1_1],
        ca_certs_file=config.ca_file,
    )
    servers = [
        Server(uri, use_ssl=True, tls=tls, get_info=NONE, connect_timeout=config.connect_timeout)
        for uri in config.uris
    ]

    def make(bind_dn: str, password: str) -> list[Connection]:
        return [
            Connection(
                server,
                user=bind_dn,
                password=password,
                authentication=SIMPLE,
                auto_referrals=False,
                receive_timeout=config.receive_timeout,
                raise_exceptions=False,
                read_only=True,
            )
            for server in servers
        ]

    return make


def _is_tls_problem(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(word in text for word in ("certificate", "ssl", "tls", "hostname"))


class AdDirectory:
    def __init__(self, config: LdapConfig, factory: ConnectionFactory | None = None) -> None:
        self.config = config
        self.factory = factory or ldaps_factory(config)

    def normalize(self, raw: str) -> str:
        return normalize_login(raw, self.config.login_attribute, self.config.upn_suffix)

    def _bind(self, bind_dn: str, password: str) -> Connection | None:
        """First reachable server: bound connection, or None if the credentials are wrong.

        Raises AdUnavailable when no server can be reached (or TLS fails on all of them).
        """
        last: BaseException | None = None
        for connection in self.factory(bind_dn, password):
            try:
                if connection.bind():
                    return connection
                return None  # reachable server, wrong credentials
            except (LDAPSocketOpenError, LDAPCommunicationError, ssl.SSLError, OSError) as exc:
                last = exc
                continue
        if last is not None and _is_tls_problem(last):
            log.error(
                "Active Directory: TLS/certificate verification failed (%s). Check LDAP_CA_FILE "
                "and that LDAP_URI uses the host name in the certificate.",
                last,
            )
            raise AdUnavailable("tls")
        log.error("Active Directory: no domain controller reachable (%s)", last)
        raise AdUnavailable("unreachable")

    def authenticate(self, login: str, password: str) -> AdUser | None:
        """AdUser for valid credentials; None for anything that is not a valid, enabled login."""
        if not password or not password.strip():
            return None  # an empty simple bind may succeed anonymously: never send it
        try:
            service = self._bind(self.config.bind_dn, self.config.bind_password)
            if service is None:
                log.error("Active Directory: the service account bind was refused (LDAP_BIND_DN)")
                raise AdUnavailable("service_bind")
            try:
                entry = self._find_user(service, login)
                if entry is None:
                    return None
                attrs: dict[str, Any] = entry["attributes"]
                if self._disabled(attrs):
                    return None
                user_conn = self._bind(entry["dn"], password)
                if user_conn is None:
                    return None
                user_conn.unbind()
                groups = self._groups(service, entry["dn"])
            finally:
                service.unbind()
        except AdUnavailable:
            raise
        except LDAPException:
            log.exception("Active Directory: unexpected LDAP error")
            raise AdUnavailable("error") from None
        return AdUser(
            object_guid=uuid.UUID(bytes_le=bytes(entry["raw_attributes"]["objectGUID"][0])),
            dn=entry["dn"],
            login=login,
            display_name=_first(attrs.get("displayName")),
            email=_first(attrs.get("mail")),
            groups=groups,
        )

    def find(self, login: str) -> tuple[dict[str, Any], tuple[str, ...]] | None:
        """Diagnostics (`baza-wiedzy ad-check`): the user's attributes and groups, no user bind."""
        service = self._bind(self.config.bind_dn, self.config.bind_password)
        if service is None:
            raise AdUnavailable("service_bind")
        try:
            entry = self._find_user(service, login)
            if entry is None:
                return None
            return entry, self._groups(service, entry["dn"])
        finally:
            service.unbind()

    def _find_user(self, conn: Connection, login: str) -> dict[str, Any] | None:
        value = escape_filter_chars(login)
        search_filter = f"(&{self.config.user_filter}({self.config.login_attribute}={value}))"
        conn.search(
            self.config.base_dn,
            search_filter,
            SUBTREE,
            attributes=USER_ATTRIBUTES,
            size_limit=2,
            time_limit=self.config.receive_timeout,
        )
        entries = [r for r in conn.response or [] if r.get("type") == "searchResEntry"]
        if len(entries) > 1:
            log.error("Active Directory: more than one entry for one login; refusing the login")
            return None
        if not entries or not entries[0].get("raw_attributes", {}).get("objectGUID"):
            return None
        found: dict[str, Any] = entries[0]
        return found

    @staticmethod
    def _disabled(attrs: dict[str, Any]) -> bool:
        try:
            return bool(int(_first(attrs.get("userAccountControl")) or 0) & UAC_ACCOUNTDISABLE)
        except ValueError:
            return True

    def _groups(self, conn: Connection, user_dn: str) -> tuple[str, ...]:
        dn = escape_filter_chars(user_dn)
        member = f"member:{MATCHING_RULE_IN_CHAIN}:" if self.config.nested_groups else "member"
        results = conn.extend.standard.paged_search(
            self.config.base_dn,
            f"(&(objectClass=group)({member}={dn}))",
            SUBTREE,
            attributes=["distinguishedName"],
            paged_size=self.config.page_size,
            generator=True,
            time_limit=self.config.receive_timeout,
        )
        return tuple(sorted({r["dn"] for r in results if r.get("type") == "searchResEntry"}))


def _first(value: Any) -> str | None:
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    return str(value) if value not in (None, "") else None
