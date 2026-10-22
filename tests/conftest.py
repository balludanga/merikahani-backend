import uuid

import pytest
from fastapi.testclient import TestClient
from app.main import app
from unittest.mock import patch

@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.core import ratelimit

    for limiter in (
        ratelimit.ip_attempts, ratelimit.login_failures, ratelimit.registrations, ratelimit.actions,
    ):
        limiter.clear()
    yield


@pytest.fixture(scope="module")
def test_client():
    with patch("app.api.endpoints.auth.requests.get") as mock_get, patch(
        "app.api.endpoints.auth.settings.GOOGLE_CLIENT_ID", "test-google-client-id"
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "email": "testuser@example.com",
            "name": "Test User",
            "picture": "http://example.com/avatar.png",
            "aud": "test-google-client-id",
            "email_verified": "true",
        }
        client = TestClient(app)
        yield client


def _register_and_login(client):
    unique = uuid.uuid4().hex[:8]
    payload = {
        "email": f"writer_{unique}@example.com",
        "username": f"writer_{unique}",
        "password": "secret123",
        "full_name": f"Writer {unique}",
    }
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201, response.text
    login = client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert login.status_code == 200, login.text
    data = login.json()
    return {
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
        "user": data["user"],
    }


@pytest.fixture(scope="module")
def writer_a(test_client):
    return _register_and_login(test_client)


@pytest.fixture(scope="module")
def writer_b(test_client):
    return _register_and_login(test_client)
