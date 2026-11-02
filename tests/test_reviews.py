"""Editorial review: a reviewer sends a story back with notes, the writer fixes it, a reviewer republishes."""

from unittest.mock import patch

import pytest

ADMIN_EMAIL = "testuser@example.com"  # the address the mocked Google reply returns


@pytest.fixture(autouse=True)
def _admin_is_listed():
    with patch("app.core.admin.settings.ADMIN_EMAILS", ADMIN_EMAIL):
        yield


@pytest.fixture(scope="module")
def admin(test_client):
    google = test_client.post("/api/auth/google-login", json={"token": "a" * 80})
    assert google.status_code == 200, google.text
    return {"headers": {"Authorization": f"Bearer {google.json()['access_token']}"}, "user": google.json()["user"]}


def _story(test_client, who, title, published=1, content="पहला वाक्य। यह एक अनुचित अंश है। आख़िरी वाक्य।"):
    response = test_client.post(
        "/api/posts", json={"title": title, "content": content, "published": published}, headers=who["headers"]
    )
    assert response.status_code == 201, response.text
    return response.json()


def _note(test_client, admin, post, **over):
    body = {"text": "यह एक अनुचित अंश है।", "start": 14, "category": "language", "comment": "कृपया भाषा बदलें।"}
    body.update(over)
    response = test_client.post(f"/api/reviews/post/{post['id']}/notes", json=body, headers=admin["headers"])
    assert response.status_code == 201, response.text
    return response.json()


def _kinds(test_client, who):
    rows = test_client.get("/api/notifications", headers=who["headers"]).json()
    return [row["kind"] for row in rows]


def test_only_a_verified_reviewer_can_review(test_client, writer_a, admin):
    me = test_client.get("/api/auth/me", headers=admin["headers"]).json()
    assert me["is_admin"] is True
    assert test_client.get("/api/auth/me", headers=writer_a["headers"]).json()["is_admin"] is False

    assert test_client.get("/api/reviews/queue", headers=writer_a["headers"]).status_code == 404
    assert test_client.get("/api/reviews/queue").status_code == 404
    post = _story(test_client, writer_a, "सिर्फ़ समीक्षक")
    body = {"text": "पहला वाक्य", "comment": "x"}
    assert test_client.post(f"/api/reviews/post/{post['id']}/notes", json=body, headers=writer_a["headers"]).status_code == 404
    assert test_client.get("/api/reviews/queue", headers=admin["headers"]).status_code == 200


def test_a_private_draft_is_never_open_to_a_reviewer(test_client, writer_a, admin):
    draft = _story(test_client, writer_a, "निजी मसौदा", published=0)
    assert test_client.get(f"/api/posts/{draft['id']}", headers=admin["headers"]).status_code == 404
    assert test_client.get(f"/api/reviews/post/{draft['id']}", headers=admin["headers"]).status_code == 404
    body = {"text": "पहला वाक्य", "comment": "x"}
    assert test_client.post(f"/api/reviews/post/{draft['id']}/notes", json=body, headers=admin["headers"]).status_code == 404


def test_full_review_cycle(test_client, writer_a, writer_b, admin):
    post = _story(test_client, writer_a, "समीक्षा की रचना")
    slug = post["slug"]
    assert test_client.get(f"/api/posts/slug/{slug}").status_code == 200

    # 1. Notes start as drafts: the writer does not see them.
    note = _note(test_client, admin, post, suggestion="यह एक उचित अंश है।")
    assert note["draft"] is True and note["status"] == "open"
    seen = test_client.get(f"/api/reviews/post/{post['id']}", headers=writer_a["headers"]).json()
    assert seen["reviewer"] is False and seen["notes"] == []
    assert len(test_client.get(f"/api/reviews/post/{post['id']}", headers=admin["headers"]).json()["notes"]) == 1
    assert test_client.get(f"/api/reviews/post/{post['id']}", headers=writer_b["headers"]).status_code == 404

    # 2. Request changes: hidden from the public, visible to the writer and the reviewer.
    sent = test_client.post(
        f"/api/reviews/post/{post['id']}/request-changes", json={"summary": "कुछ अंश बदलें"}, headers=admin["headers"]
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["post"]["moderation_state"] == "changes_requested" and sent.json()["post"]["published"] == 0
    assert test_client.get(f"/api/posts/slug/{slug}").status_code == 404
    assert test_client.get(f"/api/posts/slug/{slug}", headers=writer_b["headers"]).status_code == 404
    assert test_client.get(f"/api/posts/slug/{slug}", headers=writer_a["headers"]).status_code == 200
    assert test_client.get(f"/api/posts/slug/{slug}", headers=admin["headers"]).status_code == 200
    assert slug not in test_client.get("/api/sitemap.xml").text
    assert test_client.get(f"/api/share/post/{slug}").status_code == 404
    listed = test_client.get("/api/posts").json()
    assert post["id"] not in [item["id"] for item in listed]

    seen = test_client.get(f"/api/reviews/post/{post['id']}", headers=writer_a["headers"]).json()
    assert [n["comment"] for n in seen["notes"]] == ["कृपया भाषा बदलें।"]
    assert seen["notes"][0]["suggestion"] == "यह एक उचित अंश है।" and seen["notes"][0]["draft"] is False
    assert seen["rounds"][0]["summary"] == "कुछ अंश बदलें"
    assert "review_changes" in _kinds(test_client, writer_a)
    told = [n for n in test_client.get("/api/notifications", headers=writer_a["headers"]).json() if n["kind"] == "review_changes"]
    assert told[0]["actor"]["username"] == "समीक्षा टीम" and told[0]["count"] == 1  # not the reviewer's account
    # The reviewer is not named to the writer.
    assert "reviewer_id" not in test_client.get(f"/api/reviews/post/{post['id']}", headers=writer_a["headers"]).text

    # 3. The writer fixes it and ticks the note off; a stranger cannot touch it.
    nid = seen["notes"][0]["id"]
    assert test_client.put(f"/api/reviews/notes/{nid}/status", json={"status": "fixed"}, headers=writer_b["headers"]).status_code == 404
    ok = test_client.put(f"/api/reviews/notes/{nid}/status", json={"status": "fixed"}, headers=writer_a["headers"])
    assert ok.status_code == 200 and ok.json()["status"] == "fixed"

    # "Publish" cannot bypass the review: it resubmits and the story stays hidden.
    edited = test_client.put(
        f"/api/posts/{post['id']}",
        json={"content": "पहला वाक्य। यह एक उचित अंश है। आख़िरी वाक्य।", "published": 1},
        headers=writer_a["headers"],
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["published"] == 0 and edited.json()["moderation_state"] == "resubmitted"
    assert test_client.get(f"/api/posts/slug/{slug}").status_code == 404
    assert "review_resubmit" in _kinds(test_client, admin)

    queue = test_client.get("/api/reviews/queue?view=resubmitted", headers=admin["headers"]).json()
    item = next(row for row in queue if row["id"] == post["id"])
    assert item["fixed_notes"] == 1 and item["open_notes"] == 0 and item["summary"] == "कुछ अंश बदलें"
    assert test_client.get("/api/reviews/queue/counts", headers=admin["headers"]).json()["resubmitted"] >= 1

    # 4. A reviewer approves it: public again at the same address.
    approved = test_client.post(f"/api/reviews/post/{post['id']}/approve", headers=admin["headers"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["post"]["published"] == 1 and approved.json()["post"]["moderation_state"] == "none"
    live = test_client.get(f"/api/posts/slug/{slug}")
    assert live.status_code == 200 and live.json()["moderation_state"] == "none"
    assert "review_approved" in _kinds(test_client, writer_a)
    assert test_client.post(f"/api/reviews/post/{post['id']}/approve", headers=admin["headers"]).status_code == 400


def test_request_changes_needs_something_to_say(test_client, writer_a, admin):
    post = _story(test_client, writer_a, "खाली समीक्षा")
    empty = test_client.post(f"/api/reviews/post/{post['id']}/request-changes", json={}, headers=admin["headers"])
    assert empty.status_code == 400
    assert test_client.get(f"/api/posts/slug/{post['slug']}").status_code == 200  # still live


def test_writer_can_resubmit_without_a_publish_click(test_client, writer_a, admin):
    post = _story(test_client, writer_a, "सीधा पुनः-प्रस्तुत")
    _note(test_client, admin, post)
    test_client.post(f"/api/reviews/post/{post['id']}/request-changes", json={}, headers=admin["headers"])
    again = test_client.post(f"/api/reviews/post/{post['id']}/resubmit", headers=writer_a["headers"])
    assert again.status_code == 200 and again.json()["post"]["moderation_state"] == "resubmitted"
    assert test_client.post(f"/api/reviews/post/{post['id']}/resubmit", headers=writer_a["headers"]).status_code == 200  # idempotent
    other = _story(test_client, writer_a, "कुछ माँगा नहीं गया")
    assert test_client.post(f"/api/reviews/post/{other['id']}/resubmit", headers=writer_a["headers"]).status_code == 400


def test_notes_can_be_edited_and_deleted_by_a_reviewer_only(test_client, writer_a, admin):
    post = _story(test_client, writer_a, "टिप्पणी बदलना")
    note = _note(test_client, admin, post)
    assert test_client.put(
        f"/api/reviews/notes/{note['id']}", json={"comment": "x"}, headers=writer_a["headers"]
    ).status_code == 404
    edited = test_client.put(
        f"/api/reviews/notes/{note['id']}",
        json={"comment": "नई टिप्पणी", "category": "hate", "suggestion": "नया पाठ"},
        headers=admin["headers"],
    )
    assert edited.json()["comment"] == "नई टिप्पणी" and edited.json()["category"] == "hate"
    cleared = test_client.put(f"/api/reviews/notes/{note['id']}", json={"suggestion": ""}, headers=admin["headers"])
    assert cleared.json()["suggestion"] is None
    assert test_client.delete(f"/api/reviews/notes/{note['id']}", headers=writer_a["headers"]).status_code == 404
    assert test_client.delete(f"/api/reviews/notes/{note['id']}", headers=admin["headers"]).status_code == 204


def test_bad_input_is_tidied_not_trusted(test_client, writer_a, admin):
    post = _story(test_client, writer_a, "ग़लत इनपुट")
    odd = _note(test_client, admin, post, category="<script>", comment="  ठीक करें  ", start=-5)
    assert odd["category"] == "other" and odd["comment"] == "ठीक करें" and odd["start_offset"] is None
    blank = test_client.post(
        f"/api/reviews/post/{post['id']}/notes", json={"text": "पहला वाक्य", "comment": "   "}, headers=admin["headers"]
    )
    assert blank.status_code == 422


def test_deleting_a_story_under_review_does_not_fail(test_client, writer_a, admin):
    post = _story(test_client, writer_a, "हटाई जाने वाली")
    _note(test_client, admin, post)
    test_client.post(f"/api/reviews/post/{post['id']}/request-changes", json={"summary": "s"}, headers=admin["headers"])
    assert test_client.delete(f"/api/posts/{post['id']}", headers=writer_a["headers"]).status_code == 204
