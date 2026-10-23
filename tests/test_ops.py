"""The owner's status page: closed to everyone but the one verified admin, and honest about errors."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.errorlog import describe
from app.database import SessionLocal
from app.main import app
from app.models.server_error import ServerError

ADMIN_EMAIL = "testuser@example.com"  # the address the mocked Google reply returns


def _admin_headers(test_client):
    google = test_client.post("/api/auth/google-login", json={"token": "a" * 80})
    assert google.status_code == 200, google.text
    return {"Authorization": f"Bearer {google.json()['access_token']}"}


def test_everyone_else_gets_a_plain_404(test_client, writer_a):
    with patch("app.api.endpoints.ops.settings.ADMIN_EMAILS", ADMIN_EMAIL):
        assert test_client.get("/api/ops/overview").status_code == 404  # signed out
        assert test_client.get("/api/ops/overview", headers={"Authorization": "Bearer nonsense"}).status_code == 404
        assert test_client.get("/api/ops/overview", headers=writer_a["headers"]).status_code == 404  # signed in, not admin
        assert test_client.get("/api/ops/nothing-here").status_code == 404  # what a made-up address says


def test_nobody_is_admin_when_the_list_is_empty(test_client):
    headers = _admin_headers(test_client)
    with patch("app.api.endpoints.ops.settings.ADMIN_EMAILS", ""):
        assert test_client.get("/api/ops/overview", headers=headers).status_code == 404


def test_a_password_account_with_the_admin_address_is_not_admin(test_client):
    """Anyone can register with any address; only Google vouching for it counts."""
    email = "owner-lookalike@example.com"
    test_client.post("/api/auth/register", json={"email": email, "username": "lookalike", "password": "secret123"})
    login = test_client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    with patch("app.api.endpoints.ops.settings.ADMIN_EMAILS", email):
        assert test_client.get("/api/ops/overview", headers=headers).status_code == 404


def test_the_verified_admin_gets_the_overview(test_client, writer_a):
    headers = _admin_headers(test_client)
    with patch("app.api.endpoints.ops.settings.ADMIN_EMAILS", f"someone@else.com, {ADMIN_EMAIL.upper()}"):
        response = test_client.get("/api/ops/overview", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["totals"]["users"] >= 2 and data["totals"]["google_users"] >= 1
    assert data["database"]["kind"] in ("sqlite", "postgresql")
    assert data["database"]["size_bytes"] > 0
    assert len(data["daily"]) == 30 and data["daily"][-1]["signups"] >= 1
    assert data["recent"]["signups_7d"] >= 1
    assert data["errors"]["last_24h"] >= 0
    # Nothing in it identifies anyone by email.
    assert "@" not in response.text.replace("someone@else.com", "")


def test_server_errors_are_recorded_without_personal_data(test_client):
    def boom():
        raise RuntimeError("could not save [SQL: INSERT INTO users VALUES (?)] [parameters: ('a@b.com',)]")

    app.add_api_route("/api/_boom", boom, methods=["GET"])
    before = SessionLocal().query(ServerError).count()
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/_boom?token=secret").status_code == 500

    db = SessionLocal()
    try:
        row = db.query(ServerError).order_by(ServerError.id.desc()).first()
        assert db.query(ServerError).count() == before + 1
        assert (row.method, row.path, row.status) == ("GET", "/api/_boom", 500)  # no query string
        assert row.error == "RuntimeError: could not save"
        assert "a@b.com" not in row.error and "secret" not in row.path
    finally:
        db.close()

    headers = _admin_headers(test_client)
    with patch("app.api.endpoints.ops.settings.ADMIN_EMAILS", ADMIN_EMAIL):
        errors = test_client.get("/api/ops/overview", headers=headers).json()["errors"]
    assert errors["last_24h"] >= 1 and errors["latest"][0]["path"] == "/api/_boom"


def test_describe_keeps_the_class_and_first_line_only():
    assert describe(ValueError("")) == "ValueError"
    assert describe(ValueError("x\nsecond line")) == "ValueError: x"
