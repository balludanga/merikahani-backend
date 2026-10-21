"""Tests for per-account text highlights."""

from tests.test_posts import _create_post


def test_highlight_roundtrip(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "हाइलाइट परीक्षा")
    passage = "यह एक परीक्षण लेख है।"

    created = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": passage},
        headers=writer_a["headers"],
    )
    assert created.status_code == 201, created.text
    highlight = created.json()
    assert highlight["text"] == passage
    assert highlight["post_id"] == post["id"]

    # Re-highlighting the same passage returns the existing row
    duplicate = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": passage},
        headers=writer_a["headers"],
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == highlight["id"]

    listed = test_client.get(
        "/api/highlights", params={"post_id": post["id"]}, headers=writer_a["headers"]
    )
    assert listed.status_code == 200
    assert any(item["id"] == highlight["id"] for item in listed.json())

    removed = test_client.delete(
        f"/api/highlights/{highlight['id']}", headers=writer_a["headers"]
    )
    assert removed.status_code == 204
    after = test_client.get("/api/highlights", headers=writer_a["headers"])
    assert all(item["id"] != highlight["id"] for item in after.json())


def test_highlights_are_private(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "निजी हाइलाइट")
    created = test_client.post(
        "/api/highlights",
        json={"post_id": post["id"], "text": "दूसरा अंश यहाँ"},
        headers=writer_a["headers"],
    )
    assert created.status_code == 201

    # Another reader neither sees nor can delete them
    other_view = test_client.get("/api/highlights", headers=writer_b["headers"])
    assert other_view.status_code == 200
    assert all(item["id"] != created.json()["id"] for item in other_view.json())

    denied = test_client.delete(
        f"/api/highlights/{created.json()['id']}", headers=writer_b["headers"]
    )
    assert denied.status_code == 403


def test_highlight_requires_auth(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "अनधिकृत हाइलाइट")
    response = test_client.post(
        "/api/highlights", json={"post_id": post["id"], "text": "बिना खाते के"}
    )
    assert response.status_code == 401
