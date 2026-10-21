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
