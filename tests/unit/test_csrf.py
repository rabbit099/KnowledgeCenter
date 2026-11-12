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
