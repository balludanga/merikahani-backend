import uuid
from unittest.mock import patch

import pytest

from app.core.config import settings


@pytest.fixture
def indexnow():
    """IndexNow switched on, with the network call captured."""
    with patch.object(settings, "INDEXNOW_ENABLED", True), patch("app.core.indexnow.requests.post") as post:
        post.return_value.status_code = 200
        yield post


def _submitted(post_mock):
    return [call.kwargs["json"] for call in post_mock.call_args_list]


def _make(client, headers, **fields):
    body = {"title": f"सूचना {uuid.uuid4().hex[:6]}", "content": "पाठ", "published": 1, **fields}
    response = client.post("/api/posts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_publishing_notifies_search_engines(test_client, writer_a, indexnow):
    tag = f"सूचना{uuid.uuid4().hex[:5]}"
    story = _make(test_client, writer_a["headers"], tags=[tag])
    (sent,) = _submitted(indexnow)
    assert sent["host"] == "lekhanii.vercel.app"
    assert sent["key"] == settings.INDEXNOW_KEY
    assert sent["keyLocation"] == f"https://lekhanii.vercel.app/{settings.INDEXNOW_KEY}.txt"
    urls = sent["urlList"]
    assert any(u.startswith("https://lekhanii.vercel.app/post/") and u.isascii() for u in urls)
    assert "https://lekhanii.vercel.app/" in urls
    assert f"https://lekhanii.vercel.app/user/{story['author_id']}" in urls
    assert any("/tag/" in u for u in urls)


def test_drafts_are_never_announced(test_client, writer_a, indexnow):
    draft = _make(test_client, writer_a["headers"], published=0)
    test_client.put(f"/api/posts/{draft['id']}", json={"content": "बदला हुआ"}, headers=writer_a["headers"])
    assert indexnow.call_count == 0


def test_publish_edit_unpublish_rename_and_delete_all_notify(test_client, writer_a, indexnow):
    draft = _make(test_client, writer_a["headers"], published=0)
    h = writer_a["headers"]

    test_client.put(f"/api/posts/{draft['id']}", json={"published": 1}, headers=h)          # went public
    assert indexnow.call_count == 1
    renamed = test_client.put(f"/api/posts/{draft['id']}", json={"title": f"नया नाम {uuid.uuid4().hex[:6]}"}, headers=h).json()
    urls = _submitted(indexnow)[-1]["urlList"]
    assert len(set(u for u in urls if "/post/" in u)) == 2  # new address and the old one (now a 404)
    test_client.put(f"/api/posts/{draft['id']}", json={"published": 0}, headers=h)          # went private
    assert indexnow.call_count == 3
    test_client.put(f"/api/posts/{draft['id']}", json={"content": "फिर भी निजी"}, headers=h)  # private edit: silent
    assert indexnow.call_count == 3
    test_client.put(f"/api/posts/{draft['id']}", json={"published": 1}, headers=h)
    before = indexnow.call_count
    assert test_client.delete(f"/api/posts/{renamed['id']}", headers=h).status_code == 204
    assert indexnow.call_count == before + 1


def test_a_search_engine_outage_never_breaks_publishing(test_client, writer_a):
    import requests

    with patch.object(settings, "INDEXNOW_ENABLED", True), patch(
        "app.core.indexnow.requests.post", side_effect=requests.ConnectionError("down")
    ):
        assert _make(test_client, writer_a["headers"])["published"] == 1


def test_indexnow_is_off_by_default_outside_production(test_client, writer_a):
    assert settings.debug and settings.INDEXNOW_ENABLED is None
    with patch("app.core.indexnow.requests.post") as post:
        _make(test_client, writer_a["headers"])
    assert post.call_count == 0
