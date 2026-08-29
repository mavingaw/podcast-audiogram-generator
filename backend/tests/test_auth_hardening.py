"""Making the sign-in form safe to expose.

Once this answers on a public address the sign-in form is the whole security
boundary. These cover the three things that had to change before it could be:
registration open to anybody, unlimited password guesses, and a session cookie
sent in clear text.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services import throttle
from tests.test_api import create_test_client


@pytest.fixture(autouse=True)
def clear_throttle():
    throttle.reset()
    yield
    throttle.reset()


def started(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    return client


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_registration_is_closed_by_default(monkeypatch, tmp_path):
    """It used to be open, which was fine on a LAN and not on the internet."""
    client = started(monkeypatch, tmp_path)
    assert client.get("/api/auth/signup").json()["open"] is False
    response = client.post(
        "/api/auth/register", json={"username": "guest", "password": "another-password"}
    )
    assert response.status_code == 403


def test_an_admin_can_open_registration(monkeypatch, tmp_path):
    client = started(monkeypatch, tmp_path)
    client.put("/api/settings/signups", json={"open": True})
    assert client.get("/api/auth/signup").json()["open"] is True
    assert client.post(
        "/api/auth/register", json={"username": "guest", "password": "another-password"}
    ).status_code == 200


def test_a_code_lets_somebody_in_without_opening_the_door(monkeypatch, tmp_path):
    client = started(monkeypatch, tmp_path)
    import app.api.routes as routes

    monkeypatch.setattr(
        routes, "settings", dataclasses.replace(routes.settings, signup_code="let-me-in")
    )
    # Registration is still closed...
    assert client.get("/api/auth/signup").json()["open"] is False
    # ...but the code works.
    assert client.post(
        "/api/auth/register",
        json={"username": "guest", "password": "another-password", "code": "let-me-in"},
    ).status_code == 200


def test_the_wrong_code_is_refused(monkeypatch, tmp_path):
    client = started(monkeypatch, tmp_path)
    import app.api.routes as routes

    monkeypatch.setattr(
        routes, "settings", dataclasses.replace(routes.settings, signup_code="let-me-in")
    )
    for index, attempt in enumerate(("", "nope", "LET-ME-IN")):
        assert client.post(
            "/api/auth/register",
            json={"username": f"guest{index}", "password": "another-password",
                  "code": attempt},
        ).status_code == 403


def test_a_pasted_code_tolerates_stray_whitespace(monkeypatch, tmp_path):
    """People paste codes out of a message, and often bring a space with them."""
    client = started(monkeypatch, tmp_path)
    import app.api.routes as routes

    monkeypatch.setattr(
        routes, "settings", dataclasses.replace(routes.settings, signup_code="let-me-in")
    )
    assert client.post(
        "/api/auth/register",
        json={"username": "guest", "password": "another-password", "code": "  let-me-in "},
    ).status_code == 200


# --------------------------------------------------------------------------
# Guessing
# --------------------------------------------------------------------------


def test_repeated_failures_are_slowed_down(monkeypatch, tmp_path):
    client = started(monkeypatch, tmp_path)
    for _ in range(throttle.FREE_ATTEMPTS):
        assert client.post(
            "/api/auth/login", json={"username": "mujin", "password": "wrong"}
        ).status_code == 401

    blocked = client.post("/api/auth/login", json={"username": "mujin", "password": "wrong"})
    assert blocked.status_code in (401, 429)
    # The next one is certainly refused, with something to wait for.
    again = client.post("/api/auth/login", json={"username": "mujin", "password": "wrong"})
    assert again.status_code == 429
    assert "Retry-After" in again.headers


def test_a_correct_password_clears_the_count(monkeypatch, tmp_path):
    """Somebody who mistypes twice and then gets it right is not punished."""
    client = started(monkeypatch, tmp_path)
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "mujin", "password": "wrong"})
    assert client.post(
        "/api/auth/login", json={"username": "mujin", "password": "long-password"}
    ).status_code == 200

    key = [k for k in throttle._records] if throttle._records else []
    assert key == [], "failures were not forgotten after a successful sign-in"


def test_the_delay_grows_and_is_capped():
    throttle.reset()
    delays = [throttle.record_failure("someone") for _ in range(20)]
    penalised = [d for d in delays if d > 0]
    assert penalised == sorted(penalised), "the wait must not get shorter"
    assert max(penalised) <= throttle.MAX_DELAY


def test_throttling_is_per_account_and_address():
    """Guessing one account must not lock out somebody else on the same box."""
    throttle.reset()

    class FakeRequest:
        headers = {"cf-connecting-ip": "203.0.113.9"}
        client = None

    a = throttle.key_for(FakeRequest(), "mujin")
    b = throttle.key_for(FakeRequest(), "someone-else")
    assert a != b

    for _ in range(throttle.FREE_ATTEMPTS + 2):
        throttle.record_failure(a)
    assert throttle.retry_after(a) > 0
    assert throttle.retry_after(b) == 0


def test_the_forwarded_address_is_used_behind_a_tunnel():
    """The socket address behind Cloudflare is the tunnel, not the caller."""

    class FakeRequest:
        headers = {"cf-connecting-ip": "203.0.113.9"}
        client = type("C", (), {"host": "172.20.0.2"})()

    assert "203.0.113.9" in throttle.key_for(FakeRequest(), "mujin")


# --------------------------------------------------------------------------
# The session cookie
# --------------------------------------------------------------------------


def test_the_cookie_is_secure_over_https(monkeypatch, tmp_path):
    client = started(monkeypatch, tmp_path)
    response = client.post(
        "/api/auth/login",
        json={"username": "mujin", "password": "long-password"},
        headers={"x-forwarded-proto": "https"},
    )
    assert "secure" in response.headers["set-cookie"].lower()


def test_the_cookie_is_not_secure_over_plain_http(monkeypatch, tmp_path):
    """Forcing it on would break sign-in on the LAN."""
    client = started(monkeypatch, tmp_path)
    response = client.post(
        "/api/auth/login", json={"username": "mujin", "password": "long-password"}
    )
    assert "secure" not in response.headers["set-cookie"].lower()


def test_the_cookie_is_always_httponly(monkeypatch, tmp_path):
    client = started(monkeypatch, tmp_path)
    response = client.post(
        "/api/auth/login", json={"username": "mujin", "password": "long-password"}
    )
    assert "httponly" in response.headers["set-cookie"].lower()
