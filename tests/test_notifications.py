"""Authors are told when readers comment, clap or highlight."""

from tests.test_posts import _create_post


def _inbox(client, user):
    response = client.get("/api/notifications", headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _unread(client, user):
    return client.get("/api/notifications/unread-count", headers=user["headers"]).json()["count"]


def test_comment_notifies_the_author(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "प्रतिक्रिया की सूचना")
    before = _unread(test_client, writer_a)
    comment = test_client.post(
        "/api/comments",
        json={"post_id": post["id"], "content": "बहुत सुंदर रचना है"},
        headers=writer_b["headers"],
    ).json()

    assert _unread(test_client, writer_a) == before + 1
    item = next(n for n in _inbox(test_client, writer_a) if n["comment_id"] == comment["id"])
    assert item["kind"] == "comment" and item["read"] is False
    assert item["text"] == "बहुत सुंदर रचना है"
    assert item["actor"]["username"] == writer_b["user"]["username"]
    assert item["post"]["slug"] == post["slug"] and item["post"]["title"] == post["title"]
    assert "email" not in item["actor"]


def test_deleting_a_response_withdraws_its_notification(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "हटाई गई प्रतिक्रिया")
    comment = test_client.post(
        "/api/comments", json={"post_id": post["id"], "content": "जल्दी में लिखा"}, headers=writer_b["headers"]
    ).json()
    assert any(n["comment_id"] == comment["id"] for n in _inbox(test_client, writer_a))
    test_client.delete(f"/api/comments/{comment['id']}", headers=writer_b["headers"])
    assert all(n["comment_id"] != comment["id"] for n in _inbox(test_client, writer_a))


def test_many_claps_from_one_reader_are_one_notification(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "तालियों की सूचना")
    for _ in range(3):
        assert test_client.post(f"/api/posts/{post['id']}/clap", headers=writer_b["headers"]).status_code == 200
    claps = [n for n in _inbox(test_client, writer_a) if n["kind"] == "clap" and n["post"]["id"] == post["id"]]
    assert len(claps) == 1 and claps[0]["count"] == 3

    # Once the author has seen it, new claps start a new notification
    test_client.post("/api/notifications/read", json={}, headers=writer_a["headers"])
    test_client.post(f"/api/posts/{post['id']}/clap", headers=writer_b["headers"])
    claps = [n for n in _inbox(test_client, writer_a) if n["kind"] == "clap" and n["post"]["id"] == post["id"]]
    assert sorted((n["read"], n["count"]) for n in claps) == [(False, 4), (True, 3)]


def test_highlights_notify_once_per_reader_with_the_latest_passage(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "हाइलाइट की सूचना")
    for text, start in (("यह एक परीक्षण", 0), ("परीक्षण लेख है", 6)):
        assert test_client.post(
            "/api/highlights", json={"post_id": post["id"], "text": text, "start": start}, headers=writer_b["headers"]
        ).status_code == 201
    # the same passage again is not a new event
    test_client.post(
        "/api/highlights", json={"post_id": post["id"], "text": "परीक्षण लेख है", "start": 6}, headers=writer_b["headers"]
    )
    items = [n for n in _inbox(test_client, writer_a) if n["kind"] == "highlight" and n["post"]["id"] == post["id"]]
    assert len(items) == 1
    assert items[0]["count"] == 2 and items[0]["text"] == "परीक्षण लेख है"


def test_you_are_never_notified_about_yourself(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "अपनी ही रचना")
    before = _unread(test_client, writer_a)
    test_client.post("/api/comments", json={"post_id": post["id"], "content": "मेरी अपनी"}, headers=writer_a["headers"])
    test_client.post(f"/api/posts/{post['id']}/clap", headers=writer_a["headers"])
    test_client.post("/api/highlights", json={"post_id": post["id"], "text": "यह एक परीक्षण"}, headers=writer_a["headers"])
    assert _unread(test_client, writer_a) == before


def test_mark_read_and_privacy(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "पढ़ी हुई सूचना")
    test_client.post("/api/comments", json={"post_id": post["id"], "content": "पहली"}, headers=writer_b["headers"])
    test_client.post("/api/comments", json={"post_id": post["id"], "content": "दूसरी"}, headers=writer_b["headers"])
    mine = [n for n in _inbox(test_client, writer_a) if n["post"]["id"] == post["id"]]
    assert len(mine) == 2

    # Someone else's inbox never shows them, and cannot mark them read
    assert all(n["post"]["id"] != post["id"] for n in _inbox(test_client, writer_b))
    before = _unread(test_client, writer_a)
    test_client.post("/api/notifications/read", json={"ids": [n["id"] for n in mine]}, headers=writer_b["headers"])
    assert _unread(test_client, writer_a) == before

    done = test_client.post(
        "/api/notifications/read", json={"ids": [mine[0]["id"]]}, headers=writer_a["headers"]
    ).json()
    assert done["count"] == before - 1
    after = {n["id"]: n["read"] for n in _inbox(test_client, writer_a)}
    assert after[mine[0]["id"]] is True and after[mine[1]["id"]] is False

    assert test_client.post("/api/notifications/read", json={}, headers=writer_a["headers"]).json()["count"] == 0


def test_notifications_require_sign_in(test_client):
    assert test_client.get("/api/notifications").status_code == 401
    assert test_client.get("/api/notifications/unread-count").status_code == 401
    assert test_client.post("/api/notifications/read", json={}).status_code == 401


def test_deleting_a_story_removes_its_notifications(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "हटाई जाने वाली रचना")
    test_client.post("/api/comments", json={"post_id": post["id"], "content": "देखो"}, headers=writer_b["headers"])
    assert any(n["post"]["id"] == post["id"] for n in _inbox(test_client, writer_a))
    assert test_client.delete(f"/api/posts/{post['id']}", headers=writer_a["headers"]).status_code == 204
    assert all(n["post"]["id"] != post["id"] for n in _inbox(test_client, writer_a))
