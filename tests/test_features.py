"""Tests for follow, bookmark and tag features (Lekhani)."""

from tests.test_posts import _create_post


def test_follow_flow(test_client, writer_a, writer_b):
    # A follows B
    response = test_client.post(
        f"/api/follows/{writer_b['user']['id']}", headers=writer_a["headers"]
    )
    assert response.status_code == 201
    assert response.json()["following"] is True
    assert response.json()["follower_count"] >= 1

    # Profile reflects the follow
    profile = test_client.get(
        f"/api/users/{writer_b['user']['id']}", headers=writer_a["headers"]
    )
    assert profile.status_code == 200
    body = profile.json()
    assert body["follower_count"] >= 1
    assert body["is_following"] is True

    # Cannot follow yourself
    self_follow = test_client.post(
        f"/api/follows/{writer_a['user']['id']}", headers=writer_a["headers"]
    )
    assert self_follow.status_code == 400

    # Followers list contains A
    followers = test_client.get(f"/api/follows/{writer_b['user']['id']}/followers")
    assert followers.status_code == 200
    assert any(u["id"] == writer_a["user"]["id"] for u in followers.json())

    # Unfollow
    unfollow = test_client.delete(
        f"/api/follows/{writer_b['user']['id']}", headers=writer_a["headers"]
    )
    assert unfollow.status_code == 200
    assert unfollow.json()["following"] is False


def test_bookmark_flow(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_b["headers"], "बुकमार्क रचना", tags=["बुकमार्कटैग"])

    # A bookmarks B's post
    add = test_client.post(f"/api/bookmarks/{post['id']}", headers=writer_a["headers"])
    assert add.status_code == 201
    assert add.json()["bookmarked"] is True

    # Reading list contains the post
    listing = test_client.get("/api/bookmarks", headers=writer_a["headers"])
    assert listing.status_code == 200
    assert any(p["id"] == post["id"] and p["bookmarked"] for p in listing.json())

    # Remove bookmark
    remove = test_client.delete(
        f"/api/bookmarks/{post['id']}", headers=writer_a["headers"]
    )
    assert remove.status_code == 204
    listing = test_client.get("/api/bookmarks", headers=writer_a["headers"])
    assert all(p["id"] != post["id"] for p in listing.json())


def test_following_feed(test_client, writer_a, writer_b):
    # A follows B, B publishes a post
    test_client.post(f"/api/follows/{writer_b['user']['id']}", headers=writer_a["headers"])
    post = _create_post(test_client, writer_b["headers"], "फ़ॉलोइंग फ़ीड रचना")

    feed = test_client.get(
        "/api/posts", params={"feed": "following"}, headers=writer_a["headers"]
    )
    assert feed.status_code == 200
    assert any(p["id"] == post["id"] for p in feed.json())

    # Anonymous following feed is empty (not an error)
    anon = test_client.get("/api/posts", params={"feed": "following"})
    assert anon.status_code == 200
    assert anon.json() == []


def test_popular_tags(test_client, writer_b):
    _create_post(test_client, writer_b["headers"], "लोकप्रिय टैग", tags=["लोकप्रियटैग"])
    response = test_client.get("/api/tags")
    assert response.status_code == 200
    tags = response.json()
    match = [t for t in tags if t["name"] == "लोकप्रियटैग"]
    assert match and match[0]["post_count"] >= 1

    # Posts by tag slug
    slug = match[0]["slug"]
    by_tag = test_client.get(f"/api/tags/{slug}/posts")
    assert by_tag.status_code == 200
    assert any(p["tags"] and "लोकप्रियटैग" in p["tags"] for p in by_tag.json())


def test_response_can_quote_the_passage_it_is_about(test_client, writer_a, writer_b):
    post = _create_post(test_client, writer_a["headers"], "उद्धरण सहित प्रतिक्रिया")
    quoted = test_client.post(
        "/api/comments",
        json={"post_id": post["id"], "content": "बहुत सुंदर पंक्ति", "quote": "  यह   एक \n परीक्षण  "},
        headers=writer_b["headers"],
    )
    assert quoted.status_code == 201, quoted.text
    assert quoted.json()["quote"] == "यह एक परीक्षण"

    plain = test_client.post(
        "/api/comments",
        json={"post_id": post["id"], "content": "सामान्य प्रतिक्रिया", "quote": "   "},
        headers=writer_b["headers"],
    )
    assert plain.status_code == 201
    assert plain.json()["quote"] is None

    listed = test_client.get(f"/api/comments/post/{post['id']}").json()
    assert [item["quote"] for item in listed] == ["यह एक परीक्षण", None]
