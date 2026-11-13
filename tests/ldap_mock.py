"""A synthetic Active Directory on ldap3 MOCK_SYNC (no real company data).

Supports what the mock can: simple binds with userPassword, equality filters, paged searches.
It cannot evaluate LDAP_MATCHING_RULE_IN_CHAIN (nested groups) or bitwise matching rules; those
are covered by the manual test on a real AD (docs: auth-and-permissions.md).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ldap3 import MOCK_SYNC, NONE, Connection, Server

from baza_wiedzy.core.auth.ldap import AdDirectory, LdapConfig

BASE = "dc=firma,dc=local"
SERVICE_DN = f"cn=svc-bazawiedzy,ou=Service,{BASE}"
SERVICE_PASSWORD = "synthetic service password"


def config(**overrides: object) -> LdapConfig:
    values: dict[str, object] = {
        "uris": ("ldaps://dc1.firma.local",),
        "bind_dn": SERVICE_DN,
        "bind_password": SERVICE_PASSWORD,
        "base_dn": BASE,
        "login_attribute": "sAMAccountName",
        "upn_suffix": "firma.local",
        "user_filter": "(objectClass=user)",
        "ca_file": "/nonexistent/ca.pem",
        "nested_groups": False,
        "connect_timeout": 5,
        "receive_timeout": 10,
        "page_size": 2,  # small, so that group searches really use several pages
    }
    values.update(overrides)
    return LdapConfig(**values)  # type: ignore[arg-type]


@dataclass
class MockAd:
    server: Server = field(default_factory=lambda: Server("mock-ad", get_info=NONE))
    _admin: Connection | None = None

    def __post_init__(self) -> None:
        self._admin = Connection(
            self.server, user=SERVICE_DN, password=SERVICE_PASSWORD, client_strategy=MOCK_SYNC
        )
        self._admin.strategy.add_entry(
            SERVICE_DN, {"objectClass": ["top", "user"], "userPassword": SERVICE_PASSWORD}
        )

    def add_user(
        self,
        login: str,
        password: str,
        *,
        display_name: str = "Test User",
        disabled: bool = False,
        guid: uuid.UUID | None = None,
    ) -> tuple[str, uuid.UUID]:
        guid = guid or uuid.uuid4()
        dn = f"cn={login},ou=Users,{BASE}"
        assert self._admin is not None
        self._admin.strategy.add_entry(
            dn,
            {
                "objectClass": ["top", "person", "user"],
                "sAMAccountName": login,
                "userPrincipalName": f"{login}@firma.local",
                "userPassword": password,
                "objectGUID": guid.bytes_le,
                "userAccountControl": str(514 if disabled else 512),
                "displayName": display_name,
                "mail": f"{login}@firma.example",
            },
        )
        return dn, guid

    def add_group(self, name: str, members: list[str]) -> str:
        dn = f"cn={name},ou=Groups,{BASE}"
        assert self._admin is not None
        self._admin.strategy.add_entry(dn, {"objectClass": ["top", "group"], "member": members})
        return dn

    def set_members(self, group_dn: str, members: list[str]) -> None:
        assert self._admin is not None
        self._admin.strategy.entries[group_dn.lower()]["member"] = [m.encode() for m in members]

    def directory(self, **overrides: object) -> AdDirectory:
        def factory(bind_dn: str, password: str) -> list[Connection]:
            return [
                Connection(self.server, user=bind_dn, password=password, client_strategy=MOCK_SYNC)
            ]

        return AdDirectory(config(**overrides), factory=factory)
