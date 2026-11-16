"""Sign-in is by Google only in production: the email-and-password routes are closed, Google still works,
and a person who made a password account earlier keeps their stories by signing in with Google."""

import uuid

import pytest

from app.core.config import settings


@pytest.fixture
def production_like(monkeypatch):
    """What a deployment looks like when PASSWORD_SIGN_IN was never set: DEBUG off."""
    monkeypatch.setattr(settings, "PASSWORD_SIGN_IN", None)
    monkeypatch.setattr(settings, "debug", False)


def _body():
    tag = uuid.uuid4().hex[:8]
    return {"email": f"go_{tag}@example.com", "username": f"go_{tag}", "password": "secret123"}


def test_password_routes_are_open_for_local_work_and_the_tests():
    """Debug on and nothing set: the same box the suite runs on."""
    assert settings.debug and settings.PASSWORD_SIGN_IN is None


def test_password_sign_up_and_sign_in_are_closed_in_production(test_client, production_like):
    body = _body()
    refused = test_client.post("/api/auth/register", json=body)
    assert refused.status_code == 403 and "गूगल" in refused.json()["detail"]
    login = test_client.post("/api/auth/login", json={"email": body["email"], "password": body["password"]})
    assert login.status_code == 403 and "गूगल" in login.json()["detail"]

    # Nothing was created by the refused sign-up.
    from app.database import SessionLocal
    from app.models.user import User

    with SessionLocal() as db:
        assert db.query(User).filter(User.email == body["email"]).first() is None


def test_the_setting_can_open_them_deliberately(test_client, monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "PASSWORD_SIGN_IN", True)
    assert test_client.post("/api/auth/register", json=_body()).status_code == 201
    monkeypatch.setattr(settings, "PASSWORD_SIGN_IN", False)
    monkeypatch.setattr(settings, "debug", True)  # debug alone does not override an explicit "off"
    assert test_client.post("/api/auth/register", json=_body()).status_code == 403


def test_google_sign_in_still_works_in_production(test_client, production_like):
    response = test_client.post("/api/auth/google-login", json={"token": "mock_google_token"})
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["email"] and test_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    ).status_code == 200


def test_an_earlier_password_account_moves_to_google_and_keeps_its_stories(test_client, monkeypatch):
    """Someone who made an account with a password before signs in with the Google account that has the same
    address: it is the same account, their stories are there, and the password stops working."""
    from unittest.mock import patch

    from app.database import SessionLocal
    from app.models.user import User
    from tests.test_posts import _create_post

    body = _body()
    assert test_client.post("/api/auth/register", json=body).status_code == 201  # while the routes were open
    login = test_client.post("/api/auth/login", json={"email": body["email"], "password": body["password"]})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    story = _create_post(test_client, headers, f"पुरानी रचना {uuid.uuid4().hex[:6]}")["id"]

    monkeypatch.setattr(settings, "PASSWORD_SIGN_IN", None)
    monkeypatch.setattr(settings, "debug", False)
    claims = {"email": body["email"], "name": "Old Writer", "aud": "test-google-client-id", "email_verified": "true"}
    with patch("app.api.endpoints.auth.requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = claims
        google = test_client.post("/api/auth/google-login", json={"token": "mock_google_token"})
    assert google.status_code == 200, google.text
    assert google.json()["user"]["id"] == login.json()["user"]["id"]  # the same account
    assert test_client.get(f"/api/posts/{story}").status_code == 200  # its story is still there

    # The old password no longer works (even if the password routes were opened), and the address counts as verified.
    monkeypatch.setattr(settings, "PASSWORD_SIGN_IN", True)
    assert test_client.post("/api/auth/login", json={"email": body["email"], "password": body["password"]}).status_code == 401
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == body["email"]).one()
        assert (user.auth_provider, user.email_verified) == ("google", True)
