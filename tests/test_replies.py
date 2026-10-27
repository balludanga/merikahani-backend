"""Answering a response, being told about it, and keeping the inbox short."""

from datetime import timedelta

from app.core.clock import utcnow
from app.core.notify import KEEP_LATEST, prune_notifications
from app.database import SessionLocal
from app.models.notification import Notification
from tests.test_posts import _create_post


def _respond(client, user, post, content, **extra):
    return client.post(
        "/api/comments", json={"post_id": post["id"], "content": content, **extra}, headers=user["headers"]
    )


def _inbox(client, user):
    return client.get("/api/notifications", headers=user["headers"]).json()


def test_reply_is_threaded_and_tells_who_was_answered(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "जवाब की कहानी")
    top = _respond(test_client, writer_b, post, "पहली प्रतिक्रिया").json()
    assert top["parent_id"] is None

    reply = _respond(test_client, writer_a, post, "धन्यवाद", parent_id=top["id"])
    assert reply.status_code == 201, reply.text
    assert reply.json()["parent_id"] == top["id"]

    # The reader who was answered is told; the story's author is the replier, so not told of themselves.
    item = next(n for n in _inbox(test_client, writer_b) if n["comment_id"] == reply.json()["id"])
    assert item["kind"] == "reply" and item["text"] == "धन्यवाद"
    assert item["post"]["slug"] == post["slug"]

    listed = test_client.get(f"/api/comments/post/{post['id']}").json()
    assert [(c["id"], c["parent_id"]) for c in listed] == [(top["id"], None), (reply.json()["id"], top["id"])]


def test_reply_to_a_reply_joins_the_same_thread(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "धागे की कहानी")
    top = _respond(test_client, writer_b, post, "शुरुआत").json()
    first = _respond(test_client, writer_a, post, "पहला जवाब", parent_id=top["id"]).json()
    second = _respond(test_client, writer_b, post, "उसका जवाब", parent_id=first["id"])
    assert second.json()["parent_id"] == top["id"]
    # The author of the answer that was answered is the one told.
    assert any(n["comment_id"] == second.json()["id"] and n["kind"] == "reply" for n in _inbox(test_client, writer_a))


def test_story_author_hears_of_a_reply_between_two_readers(test_client, writer_a, writer_b, writer_c):
    post = _create_post(test_client, writer_a["headers"], "तीन लोग")
    top = _respond(test_client, writer_b, post, "मेरी राय").json()
    reply = _respond(test_client, writer_c, post, "मैं सहमत हूँ", parent_id=top["id"]).json()
    kinds = {n["kind"] for n in _inbox(test_client, writer_a) if n["comment_id"] == reply["id"]}
    assert kinds == {"comment"}  # the author is not the one answered
    assert {n["kind"] for n in _inbox(test_client, writer_b) if n["comment_id"] == reply["id"]} == {"reply"}


def test_answering_the_story_authors_response_sends_one_notification(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "एक ही सूचना")
    mine = _respond(test_client, writer_a, post, "लेखक की टिप्पणी").json()
    reply = _respond(test_client, writer_b, post, "पाठक का जवाब", parent_id=mine["id"]).json()
    mine_for_reply = [n for n in _inbox(test_client, writer_a) if n["comment_id"] == reply["id"]]
    assert [n["kind"] for n in mine_for_reply] == ["reply"]


def test_reply_must_answer_a_response_on_the_same_story(test_client, writer_a, writer_b):
    one = _create_post(test_client, writer_a["headers"], "पहली रचना")
    two = _create_post(test_client, writer_a["headers"], "दूसरी रचना")
    elsewhere = _respond(test_client, writer_b, one, "यहाँ").json()
    assert _respond(test_client, writer_b, two, "वहाँ", parent_id=elsewhere["id"]).status_code == 404
    assert _respond(test_client, writer_b, two, "कोई नहीं", parent_id=999999).status_code == 404


def test_deleting_a_response_deletes_its_answers_and_their_notifications(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "हटाने की कहानी")
    top = _respond(test_client, writer_b, post, "ऊपर वाली").json()
    reply = _respond(test_client, writer_a, post, "नीचे वाली", parent_id=top["id"]).json()
    assert any(n["comment_id"] == reply["id"] for n in _inbox(test_client, writer_b))

    assert test_client.delete(f"/api/comments/{top['id']}", headers=writer_b["headers"]).status_code == 204
    assert test_client.get(f"/api/comments/post/{post['id']}").json() == []
    assert all(n["comment_id"] != reply["id"] for n in _inbox(test_client, writer_b))


def test_a_quote_stays_on_top_level_responses_only(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "उद्धरण")
    top = _respond(test_client, writer_b, post, "अंश पर", quote="कोई अंश").json()
    assert top["quote"] == "कोई अंश"
    reply = _respond(test_client, writer_a, post, "जवाब", quote="और अंश", parent_id=top["id"]).json()
    assert reply["quote"] is None


def test_prune_drops_old_read_notifications_and_caps_the_inbox(test_client, writer_a, writer_b):
    user_id = writer_a["user"]["id"]
    actor_id = writer_b["user"]["id"]
    now = utcnow()
    db = SessionLocal()
    try:
        db.query(Notification).filter(Notification.user_id == user_id).delete()
        old_read = Notification(user_id=user_id, actor_id=actor_id, kind="follow", created_at=now - timedelta(days=90), read_at=now - timedelta(days=89))
        old_unread = Notification(user_id=user_id, actor_id=actor_id, kind="follow", created_at=now - timedelta(days=90))
        recent_read = Notification(user_id=user_id, actor_id=actor_id, kind="follow", created_at=now - timedelta(days=5), read_at=now)
        db.add_all([old_read, old_unread, recent_read])
        db.commit()
        keep = {old_unread.id, recent_read.id}

        prune_notifications(db, user_id)
        left = {n.id for n in db.query(Notification).filter(Notification.user_id == user_id)}
        assert left == keep  # an unseen one is never dropped for age

        db.add_all([Notification(user_id=user_id, actor_id=actor_id, kind="follow") for _ in range(KEEP_LATEST + 20)])
        db.commit()
        prune_notifications(db, user_id)
        assert db.query(Notification).filter(Notification.user_id == user_id).count() == KEEP_LATEST
    finally:
        db.query(Notification).filter(Notification.user_id == user_id).delete()
        db.commit()
        db.close()


def test_my_highlights_carry_their_story(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "हाइलाइट की सूची")
    made = test_client.post(
        "/api/highlights", json={"post_id": post["id"], "text": "यह एक परीक्षण"}, headers=writer_b["headers"]
    )
    assert made.status_code == 201, made.text
    mine = test_client.get("/api/highlights", headers=writer_b["headers"]).json()
    row = next(h for h in mine if h["id"] == made.json()["id"])
    assert row["post"] == {"id": post["id"], "title": post["title"], "slug": post["slug"]}


def test_a_response_remembers_where_its_passage_starts(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "स्थान की कहानी")
    made = _respond(test_client, writer_b, post, "यहाँ", quote="परीक्षण लेख", quote_start=14).json()
    assert made["quote"] == "परीक्षण लेख" and made["quote_start"] == 14
    listed = next(c for c in test_client.get(f"/api/comments/post/{post['id']}").json() if c["id"] == made["id"])
    assert listed["quote_start"] == 14

    # Nonsense positions are dropped, and a position without a passage means nothing.
    assert _respond(test_client, writer_b, post, "ऋण", quote="अंश", quote_start=-5).json()["quote_start"] is None
    assert _respond(test_client, writer_b, post, "बिना अंश", quote_start=7).json()["quote_start"] is None
    top = made
    assert _respond(test_client, writer_a, post, "जवाब", quote="x", quote_start=3, parent_id=top["id"]).json()["quote_start"] is None
