import pytest
from fastapi.testclient import TestClient
from app.main import app
from unittest.mock import patch

@pytest.fixture(scope="module")
def test_client():
    with patch("app.api.endpoints.auth.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "email": "testuser@example.com",
            "name": "Test User",
            "picture": "http://example.com/avatar.png",
            "aud": "985235744714-ei8qmafq1ah3ktk61ntg8jhoqg26nn9h.apps.googleusercontent.com"
        }
        client = TestClient(app)
        yield client