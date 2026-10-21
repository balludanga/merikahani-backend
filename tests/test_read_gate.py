"""Tests for the anonymous read gate (preview, then sign in to continue)."""

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
