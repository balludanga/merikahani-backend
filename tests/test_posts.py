def _create_post(client, headers, title, tags=None, published=1):
    payload = {
        "title": title,
        "subtitle": "उपशीर्षक",
        "content": "यह एक परीक्षण लेख है। " * 10,
        "published": published,
    }
    if tags is not None:
        payload["tags"] = tags
    response = client.post("/api/posts", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_post_with_tags(test_client, writer_a):
    post = _create_post(
        test_client,
        writer_a["headers"],
        "टैग सहित रचना",
        tags=["परीक्षण", "हिंदी", "परीक्षण"],
    )
    # Duplicate tags are de-duplicated
    assert sorted(post["tags"]) == ["परीक्षण", "हिंदी"]
    assert post["bookmarked"] is False
    assert post["my_claps"] == 0


def test_get_posts_includes_tags(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "फ़ीड में टैग", tags=["फ़ीडटैग"])
    response = test_client.get("/api/posts", params={"tag": "फ़ीडटैग"})
    assert response.status_code == 200
    posts = response.json()
    assert any(p["id"] == post["id"] and "फ़ीडटैग" in p["tags"] for p in posts)


def test_update_post_tags(test_client, writer_a):
    post = _create_post(test_client, writer_a["headers"], "टैग बदलें", tags=["पुराना"])
    response = test_client.put(
        f"/api/posts/{post['id']}",
        json={"tags": ["नया"]},
        headers=writer_a["headers"],
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["नया"]


def _create_rich_post(client, headers, title):
    body = "यह एक लंबा परीक्षण लेख है जो गेट से पहले कई बार दोहराता है। " * 20
    response = client.post(
        "/api/posts",
        json={
            "title": title,
            "content": body,
            "content_html": (
                "<script>alert(1)</script>"
                "<h2>शीर्षक</h2>"
                "<p onerror='boom()'>पैरा</p>"
                '<a href="javascript:alert(2)">खत</a>'
                '<img src="https://example.com/chit.png" alt="चिट">'
            ),
            "published": 1,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_content_html_is_sanitized_on_create_and_update(test_client, writer_a):
    post = _create_rich_post(test_client, writer_a["headers"], "सैनिटाइज़्ड रचना")
    assert "<script>" not in post["content_html"]
    assert "onerror" not in post["content_html"]
    assert "javascript:" not in post["content_html"]
    assert "<h2>शीर्षक</h2>" in post["content_html"]
    assert "https://example.com/chit.png" in post["content_html"]

    # Replacing the body with pure markup still strips every unsafe form.
    # bleach drops the tag but keeps inert text (strip=True) — that is safe.
    response = test_client.put(
        f"/api/posts/{post['id']}",
        json={"content_html": "<p>ठीक</p><script>dragostela()</script>"},
        headers=writer_a["headers"],
    )
    assert response.status_code == 200
    html = response.json()["content_html"]
    assert "<script>" not in html
    assert "<p>ठीक</p>" in html


def test_video_iframes_limited_to_allowed_hosts(test_client, writer_a):
    post = _create_rich_post(test_client, writer_a["headers"], "वीडियो सैनिटाइज़र")
    response = test_client.put(
        f"/api/posts/{post['id']}",
        json={
            "content_html": (
                '<p>मूळ</p>'
                '<iframe src="https://www.youtube.com/embed/abc123" width="640" height="360"></iframe>'
                '<iframe src="https://player.vimeo.com/video/456" width="640" height="360"></iframe>'
                '<iframe src="https://evil.example.com/embed"></iframe>'
            )
        },
        headers=writer_a["headers"],
    )
    assert response.status_code == 200
    html = response.json()["content_html"]
    assert "https://www.youtube.com/embed/abc123" in html
    assert "https://player.vimeo.com/video/456" in html
    assert "evil.example.com" not in html


def test_read_gate_hides_rich_body_from_anonymous(test_client, writer_a):
    post = _create_rich_post(test_client, writer_a["headers"], "गेट के पीछे की रचना")
    slug = post["slug"]

    anon = test_client.get(f"/api/posts/slug/{slug}")
    assert anon.status_code == 200
    payload = anon.json()
    assert payload["is_preview"] is True
    # The teaser must not leak the full body through the rich rendering.
    assert payload["content_html"] is None

    mine = test_client.get(f"/api/posts/slug/{slug}", headers=writer_a["headers"])
    assert mine.status_code == 200
    assert mine.json()["is_preview"] is False
    assert "<h2>शीर्षक</h2>" in mine.json()["content_html"]
