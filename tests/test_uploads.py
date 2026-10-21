"""Tests for the Supabase-storage upload proxy (network fully mocked)."""

import base64

import pytest
from unittest.mock import patch

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
WEBP = b"RIFF" + b"0" * 4 + b"WEBP" + b"0" * 32


def _enable_storage():
    return (
        patch("app.api.endpoints.uploads.settings.SUPABASE_URL", "https://test.supabase.co"),
        patch("app.api.endpoints.uploads.settings.SUPABASE_SERVICE_ROLE_KEY", "test-key"),
    )


@pytest.fixture
def storage_on(test_client):
    patches = _enable_storage()
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def test_upload_requires_auth(test_client):
    response = test_client.post(
        "/api/uploads/image",
        json={"data": base64.b64encode(PNG).decode(), "content_type": "image/png"},
    )
    assert response.status_code == 401


def test_upload_disabled_without_storage_config(test_client, writer_a):
    with patch("app.api.endpoints.uploads.settings.SUPABASE_URL", ""), patch(
        "app.api.endpoints.uploads.settings.SUPABASE_SERVICE_ROLE_KEY", ""
    ):
        response = test_client.post(
            "/api/uploads/image",
            json={"data": base64.b64encode(PNG).decode(), "content_type": "image/png"},
            headers=writer_a["headers"],
        )
    # No Supabase credentials -> graceful 503 (editor falls back to URL input).
    assert response.status_code == 503


def test_upload_rejects_bad_types(test_client, writer_a, storage_on):
    for payload in (
        {"data": base64.b64encode(b"not an image").decode(), "content_type": "image/svg+xml"},
        {"data": base64.b64encode(b"<html>").decode(), "content_type": "image/png"},
        {"data": "!!!not base64!!!", "content_type": "image/png"},
    ):
        response = test_client.post(
            "/api/uploads/image", json=payload, headers=writer_a["headers"]
        )
        assert response.status_code == 400, payload


def test_upload_rejects_oversized(test_client, writer_a, storage_on):
    huge = base64.b64encode(PNG + b"0" * (3 * 1024 * 1024)).decode()
    response = test_client.post(
        "/api/uploads/image",
        json={"data": huge, "content_type": "image/png"},
        headers=writer_a["headers"],
    )
    assert response.status_code == 413


def test_upload_forwards_to_storage_and_returns_public_url(test_client, writer_a, storage_on):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers

        class Resp:
            status_code = 200

        return Resp()

    with patch("app.api.endpoints.uploads.requests.post", side_effect=fake_post):
        response = test_client.post(
            "/api/uploads/image",
            json={
                "data": f"data:image/png;base64,{base64.b64encode(PNG).decode()}",
                "content_type": "image/png",
            },
            headers=writer_a["headers"],
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"].startswith(
        "https://test.supabase.co/storage/v1/object/public/lekhani-media/media/"
    )
    assert body["bytes"] == len(PNG)
    # The upload went to the authenticated endpoint with the service key.
    assert "/storage/v1/object/lekhani-media/" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["data"] == PNG


def test_upload_maps_storage_failure(test_client, writer_a, storage_on):
    class Resp:
        status_code = 400

    with patch("app.api.endpoints.uploads.requests.post", return_value=Resp()):
        response = test_client.post(
            "/api/uploads/image",
            json={"data": base64.b64encode(WEBP).decode(), "content_type": "image/webp"},
            headers=writer_a["headers"],
        )
    assert response.status_code == 502
