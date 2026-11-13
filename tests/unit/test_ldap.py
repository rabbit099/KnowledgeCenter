"""Active Directory client on the synthetic mock directory and its safety rules."""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import pytest
from ldap3 import Connection
from ldap3.core.exceptions import LDAPSocketOpenError

from baza_wiedzy.core.auth.ldap import (
    AdDirectory,
    AdUnavailable,
    LdapConfig,
    LdapConfigError,
    ldaps_factory,
    normalize_login,
)
from baza_wiedzy.core.config import Settings
from ldap_mock import MockAd, config

PASSWORD = "synthetic AD password"


@pytest.mark.parametrize(
    ("typed", "attribute", "expected"),
    [
        ("jan", "sAMAccountName", "jan"),
        ("  JAN ", "sAMAccountName", "jan"),
        ("FIRMA\\Jan", "sAMAccountName", "jan"),
        ("jan@firma.local", "sAMAccountName", "jan"),
        ("Jan@Firma.Local", "userPrincipalName", "jan@firma.local"),
        ("FIRMA\\jan", "userPrincipalName", "jan@firma.local"),
        ("jan", "userPrincipalName", "jan@firma.local"),
    ],
)
def test_normalize_login(typed: str, attribute: str, expected: str) -> None:
    assert normalize_login(typed, attribute, "firma.local") == expected


def test_valid_login_returns_identity_and_groups() -> None:
    ad = MockAd()
    dn, guid = ad.add_user("jan", PASSWORD, display_name="Jan Kowalski")
    readers = ad.add_group("BazaWiedzy-Readers", [dn])
    ad.add_group("Other", [dn])
    ad.add_group("Unrelated", [])
    user = ad.directory().authenticate("jan", PASSWORD)
    assert user is not None
    assert (user.object_guid, user.dn, user.display_name) == (guid, dn, "Jan Kowalski")
    assert readers in user.groups and len(user.groups) == 2  # two pages of size 2


@pytest.mark.parametrize("password", ["wrong password", "", "   ", "\t\n"])
def test_wrong_or_blank_password_is_refused(password: str) -> None:
    ad = MockAd()
    ad.add_user("jan", PASSWORD)
    assert ad.directory().authenticate("jan", password) is None


def test_blank_password_never_reaches_the_directory() -> None:
    calls: list[str] = []

    def factory(bind_dn: str, password: str) -> list[Connection]:
        calls.append(bind_dn)
        return []

    assert AdDirectory(config(), factory=factory).authenticate("jan", "   ") is None
    assert calls == []  # no bind at all, not even the service account


def test_disabled_and_unknown_accounts_are_refused() -> None:
    ad = MockAd()
    ad.add_user("off", PASSWORD, disabled=True)
    directory = ad.directory()
    assert directory.authenticate("off", PASSWORD) is None
    assert directory.authenticate("nobody", PASSWORD) is None


def test_filter_values_are_escaped() -> None:
    searched: list[str] = []

    class Recording:
        def __init__(self) -> None:
            self.response: list[Any] = []

        def bind(self) -> bool:
            return True

        def unbind(self) -> None:
            pass

        def search(self, base: str, search_filter: str, *args: Any, **kwargs: Any) -> bool:
            searched.append(search_filter)
            return True

    directory = AdDirectory(config(), factory=lambda dn, pw: [Recording()])
    assert directory.authenticate("jan*)(objectClass=*", PASSWORD) is None
    assert searched == ["(&(objectClass=user)(sAMAccountName=jan\\2a\\29\\28objectClass=\\2a))"]


class Unreachable:
    def __init__(self, message: str) -> None:
        self.message = message

    def bind(self) -> bool:
        raise LDAPSocketOpenError(self.message)


def test_certificate_error_is_reported_clearly(caplog: pytest.LogCaptureFixture) -> None:
    error = Unreachable("socket ssl wrapping error: certificate verify failed")
    directory = AdDirectory(config(), factory=lambda dn, pw: [error])
    with pytest.raises(AdUnavailable) as exc:
        directory.authenticate("jan", PASSWORD)
    assert exc.value.reason == "tls"
    assert "LDAP_CA_FILE" in caplog.text
    assert PASSWORD not in caplog.text


def test_unreachable_directory_and_failover() -> None:
    down = AdDirectory(config(), factory=lambda dn, pw: [Unreachable("timed out")])
    with pytest.raises(AdUnavailable) as exc:
        down.authenticate("jan", PASSWORD)
    assert exc.value.reason == "unreachable"

    ad = MockAd()
    ad.add_user("jan", PASSWORD)
    healthy = ad.directory()
    second = AdDirectory(
        config(),
        factory=lambda dn, pw: [Unreachable("timed out"), *healthy.factory(dn, pw)],
    )
    assert second.authenticate("jan", PASSWORD) is not None


def test_service_account_refused_is_unavailable() -> None:
    ad = MockAd()
    ad.add_user("jan", PASSWORD)
    with pytest.raises(AdUnavailable) as exc:
        ad.directory(bind_password="wrong service password").authenticate("jan", PASSWORD)
    assert exc.value.reason == "service_bind"


def test_real_connections_verify_certificates_and_ignore_referrals(tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic placeholder: certificates are loaded only when connecting\n")
    uris = ("ldaps://dc1.firma.local", "ldaps://dc2.firma.local")
    connections = ldaps_factory(config(uris=uris, ca_file=str(ca)))("cn=x", "y")
    assert len(connections) == 2
    for connection in connections:
        assert connection.server.ssl is True
        assert connection.server.tls.validate == ssl.CERT_REQUIRED
        assert connection.server.connect_timeout == 5
        assert connection.receive_timeout == 10
        assert connection.auto_referrals is False


def ldap_settings(tmp_path: Path, **overrides: object) -> Settings:
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    values: dict[str, Any] = {
        "ldap_enabled": True,
        "ldap_uri": "ldaps://dc1.firma.local",
        "ldap_bind_dn": "cn=svc",
        "ldap_bind_password": "x",
        "ldap_base_dn": "dc=firma,dc=local",
        "ldap_ca_file": str(ca),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]  # pydantic-settings init kwarg


def test_configuration_is_validated(tmp_path: Path) -> None:
    assert LdapConfig.from_settings(ldap_settings(tmp_path)).uris == ("ldaps://dc1.firma.local",)
    cases = {
        "must start with ldaps://": {"ldap_uri": "ldap://dc1.firma.local"},
        "LDAP_CA_FILE": {"ldap_ca_file": str(tmp_path / "missing.pem")},
        "LDAP_UPN_SUFFIX is required": {"ldap_login_attribute": "userPrincipalName"},
        "LDAP_LOGIN_ATTRIBUTE must be one of": {"ldap_login_attribute": "cn"},
        "LDAP_BIND_PASSWORD": {"ldap_bind_password": ""},
    }
    for message, overrides in cases.items():
        with pytest.raises(LdapConfigError, match=message):
            LdapConfig.from_settings(ldap_settings(tmp_path, **overrides))


def test_bind_password_from_file(tmp_path: Path) -> None:
    secret = tmp_path / "ldap_bind_password"
    secret.write_text("from a docker secret\n", encoding="utf-8")
    loaded = LdapConfig.from_settings(
        ldap_settings(tmp_path, ldap_bind_password="", ldap_bind_password_file=str(secret))
    )
    assert loaded.bind_password == "from a docker secret"
