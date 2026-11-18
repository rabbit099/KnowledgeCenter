"""CSRF middleware on a minimal application."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from baza_wiedzy.core.auth.csrf import CSRFMiddleware, make_token
from baza_wiedzy.core.auth.routes import safe_next

SECRET = "csrf-unit-test-secret-0123456789abcdef"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()

    @app.get("/form")
    def form(request: Request) -> dict[str, str]:
        return {"token": request.state.csrf_token}

    @app.post("/change")
    async def change(request: Request) -> dict[str, str]:
        form = await request.form()  # the buffered body is still readable by the route
        return {"value": str(form.get("value", ""))}

    app.add_middleware(CSRFMiddleware, secret=SECRET, secure_cookies=True)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def token(client: TestClient) -> str:
    return str(client.get("/form").json()["token"])


def test_get_sets_pre_session_cookie_and_token(client: TestClient) -> None:
    response = client.get("/form")
    pre = client.cookies.get("__Host-bw_pre")
    assert pre and "HttpOnly" in response.headers["set-cookie"]
    assert response.json()["token"] == make_token(SECRET, "p:" + pre)


def test_post_without_token_is_refused(client: TestClient) -> None:
    token(client)
    assert client.post("/change", data={"value": "1"}).status_code == 403


def test_post_with_wrong_token_is_refused(client: TestClient) -> None:
    token(client)
    response = client.post("/change", data={"value": "1", "csrf_token": "0" * 64})
    assert response.status_code == 403


def test_form_field_token_is_accepted_and_body_reaches_route(client: TestClient) -> None:
    good = token(client)
    response = client.post("/change", data={"value": "42", "csrf_token": good})
    assert response.status_code == 200
    assert response.json() == {"value": "42"}


def test_header_token_is_accepted(client: TestClient) -> None:
    good = token(client)
    response = client.post("/change", data={"value": "7"}, headers={"X-CSRF-Token": good})
    assert response.status_code == 200


def test_first_post_without_any_cookie_is_refused(client: TestClient) -> None:
    assert client.post("/change", data={"csrf_token": "x"}).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}],
)
def test_cross_site_requests_are_refused_even_with_a_valid_token(
    client: TestClient, headers: dict[str, str]
) -> None:
    good = token(client)
    response = client.post("/change", data={"csrf_token": good}, headers=headers)
    assert response.status_code == 403


def test_same_origin_header_is_accepted(client: TestClient) -> None:
    good = token(client)
    response = client.post(
        "/change", data={"csrf_token": good}, headers={"Origin": "https://testserver"}
    )
    assert response.status_code == 200


def test_token_is_bound_to_the_session_cookie(client: TestClient) -> None:
    pre_token = token(client)
    client.cookies.set("__Host-bw_session", "some-session-token")
    assert token(client) != pre_token
    assert client.post("/change", data={"csrf_token": pre_token}).status_code == 403


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/admin/users?page=2", "/admin/users?page=2"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_safe_next(target: str | None, expected: str) -> None:
    assert safe_next(target) == expected


def test_rejection_reasons() -> None:
    from baza_wiedzy.core.auth.csrf import rejection_reason

    def reason(
        *,
        cookie: bool = True,
        logged_in: bool = True,
        supplied: str | None = "a",
        same_origin: bool = True,
    ) -> str | None:
        return rejection_reason(
            has_binding_cookie=cookie,
            logged_in=logged_in,
            supplied=supplied,
            expected="a",
            same_origin=same_origin,
        )

    assert reason() is None
    assert reason(cookie=False) == "no_session"
    assert reason(same_origin=False) == "cross_site"
    assert reason(supplied=None) == "missing_token"
    assert reason(supplied="b") == "mismatch"
    assert reason(supplied="b", logged_in=False) == "session_mismatch"


def test_csrf_macro_is_always_imported_with_context() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "baza_wiedzy"
    imports = re.compile(r"\{%\s*from\s+\"admin/_macros.html\"\s+import\s+([^%]*?)\s*%\}")
    for template in root.rglob("*.html"):
        for names in imports.findall(template.read_text(encoding="utf-8")):
            if re.search(r"\bcsrf\b", names):
                assert names.endswith("with context"), f"{template}: import csrf with context"


def test_empty_token_fails_rendering() -> None:
    import pytest

    from baza_wiedzy.core.web.templating import require_csrf

    assert require_csrf("abc") == "abc"
    bad: object
    for bad in ("", None, 0):
        with pytest.raises(RuntimeError):
            require_csrf(bad)
