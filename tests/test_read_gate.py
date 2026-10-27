"""Tests for the anonymous read gate (preview, then sign in to continue)."""

from app.api.endpoints.posts import decorate_posts
from app.database import SessionLocal
from app.models.post import Post

LONG_CONTENT = "यह एक लंबी रचना का नमूना अंश है। " * 80  # well past the preview limit


def _create_long_post(test_client, writer, title):
    response = test_client.post(
        "/api/posts",
        json={
            "title": title,
            "subtitle": "उपशीर्षक",
            "content": LONG_CONTENT,
            "published": 1,
        },
        headers=writer["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_anonymous_gets_preview(test_client, writer_a):
    post = _create_long_post(test_client, writer_a, "गेट परीक्षा रचना")

    # Detail by slug
    response = test_client.get(f"/api/posts/slug/{post['slug']}")
    assert response.status_code == 200
    body = response.json()
    assert body["is_preview"] is True
    assert len(body["content"]) < len(LONG_CONTENT)
    assert body["content"].endswith("…")

    # Detail by id
    response = test_client.get(f"/api/posts/{post['id']}")
    assert response.status_code == 200
    assert response.json()["is_preview"] is True

    # Feed list
    response = test_client.get("/api/posts", params={"limit": 100})
    assert response.status_code == 200
    matched = [p for p in response.json() if p["id"] == post["id"]]
    assert matched and matched[0]["is_preview"] is True


def test_member_reads_full_content(test_client, writer_a):
    post = _create_long_post(test_client, writer_a, "पूर्ण पठन रचना")

    response = test_client.get(f"/api/posts/{post['id']}", headers=writer_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["is_preview"] is False
    assert body["content"] == LONG_CONTENT

    # The author's own stories list is never cut
    response = test_client.get("/api/posts/me", headers=writer_a["headers"])
    assert response.status_code == 200
    matched = [p for p in response.json() if p["id"] == post["id"]]
    assert matched and matched[0]["is_preview"] is False


def test_short_post_readable_without_login(test_client, writer_a):
    created = test_client.post(
        "/api/posts",
        json={
            "title": "छोटी रचना",
            "subtitle": "उपशीर्षक",
            "content": "छोटी सी बात।",
            "published": 1,
        },
        headers=writer_a["headers"],
    )
    assert created.status_code == 201, created.text
    post = created.json()

    response = test_client.get(f"/api/posts/{post['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["is_preview"] is False
    assert body["content"] == "छोटी सी बात।"


def test_gated_read_leaves_the_stored_story_intact(test_client, writer_a):
    """Decorating for a signed-out reader must not rewrite the row.

    The teaser used to be written onto the session-bound ORM instance. The request
    then closed without committing, so nothing broke — until some later code in the
    same request flushed it, quietly replacing the writer's story with its teaser.
    """
    post = _create_long_post(test_client, writer_a, "गेट से बची रचना")

    db = SessionLocal()
    try:
        row = db.query(Post).filter(Post.id == post["id"]).first()
        decorate_posts([row], None, db)  # what an anonymous request does to its rows
        db.commit()  # and what anything else in that request might then do
        db.expire_all()
        assert db.query(Post).filter(Post.id == post["id"]).first().content == LONG_CONTENT
    finally:
        db.close()

    # The teaser is still served, and the author still owns the whole story.
    anon = test_client.get(f"/api/posts/{post['id']}")
    assert anon.json()["is_preview"] is True
    assert len(anon.json()["content"]) < len(LONG_CONTENT)

    mine = test_client.get(f"/api/posts/{post['id']}", headers=writer_a["headers"])
    assert mine.json()["content"] == LONG_CONTENT
