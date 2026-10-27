"""Profile customisation: identity, links, accent, banner, photo, pins, badges."""
import base64
import warnings

from tests.conftest import _register_and_login
from tests.test_posts import _create_post

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"1" * 200


def _b64(binary):
    return base64.b64encode(binary).decode()


def _save(client, user, **fields):
    return client.put("/api/users/me/profile", json=fields, headers=user["headers"])


def _profile(client, user, viewer=None):
    headers = (viewer or {}).get("headers", {})
    return client.get(f"/api/users/{user['user']['id']}", headers=headers).json()


def test_a_writer_can_describe_themselves(test_client):
    writer = _register_and_login(test_client)
    response = _save(
        test_client, writer,
        full_name="  राम   शर्मा ", tagline="गाँव की कहानियाँ  लिखता हूँ", status_line="उपन्यास का तीसरा अध्याय",
        location="जयपुर", bio="पहली पंक्ति\n\n\n\nदूसरी   पंक्ति", accent="#C2410C",
        cover_style="dawn", genres=["कहानी", "कविता", "कहानी"],
        links=[{"label": "मेरा ब्लॉग", "url": "https://example.com/blog"}, {"url": "https://instagram.com/ram"}],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["full_name"] == "राम शर्मा"
    assert body["tagline"] == "गाँव की कहानियाँ लिखता हूँ"
    assert body["bio"] == "पहली पंक्ति\n\nदूसरी पंक्ति"
    assert body["accent"] == "#c2410c" and body["cover_style"] == "dawn"
    assert body["genres"] == ["कहानी", "कविता"]
    assert [l["url"] for l in body["links"]] == ["https://example.com/blog", "https://instagram.com/ram"]
    assert "email" not in body

    # Anyone can read it, signed in or not
    assert _profile(test_client, writer)["tagline"] == "गाँव की कहानियाँ लिखता हूँ"


def test_only_what_is_sent_changes_and_empty_clears(test_client):
    writer = _register_and_login(test_client)
    _save(test_client, writer, tagline="पहली", location="कोटा")
    body = _save(test_client, writer, tagline="").json()
    assert body["tagline"] is None and body["location"] == "कोटा"
    body = _save(test_client, writer, bio="नया परिचय").json()
    assert body["location"] == "कोटा" and body["bio"] == "नया परिचय"


def test_bad_input_is_refused(test_client):
    writer = _register_and_login(test_client)
    for bad in (
        {"accent": "red"}, {"accent": "#12345"}, {"cover_style": "neon"},
        {"genres": ["कुछ और"]}, {"genres": ["कहानी", "कविता", "ग़ज़ल", "गीत", "निबंध", "व्यंग्य"]},
        {"tagline": "क" * 101}, {"bio": "क" * 601}, {"location": "क" * 61},
        {"links": [{"url": "javascript:alert(1)"}]}, {"links": [{"url": "ftp://example.com/x"}]},
        {"links": [{"url": "https://user:pass@example.com"}]}, {"links": [{"url": "https://nodot"}]},
        {"links": [{"url": f"https://example.com/{i}"} for i in range(6)]},
        {"pinned_post_ids": [1, 2, 3, 4]},
    ):
        assert _save(test_client, writer, **bad).status_code == 422, bad


def test_profile_needs_sign_in(test_client):
    assert test_client.put("/api/users/me/profile", json={"tagline": "x"}).status_code == 401
    assert test_client.put("/api/users/me/avatar", json={"data": "x", "content_type": "image/png"}).status_code == 401


def test_photo_and_banner_are_stored_served_and_cacheable(test_client):
    writer = _register_and_login(test_client)
    uid = writer["user"]["id"]
    avatar = test_client.put(
        "/api/users/me/avatar", json={"data": _b64(PNG), "content_type": "image/png"}, headers=writer["headers"]
    )
    assert avatar.status_code == 200, avatar.text
    url = avatar.json()["avatar_url"]
    assert url.startswith(f"/api/users/{uid}/avatar?v=")

    served = test_client.get(f"/api/users/{uid}/avatar")
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert "max-age" in served.headers["cache-control"]
    again = test_client.get(f"/api/users/{uid}/avatar", headers={"If-None-Match": served.headers["etag"]})
    assert again.status_code == 304

    cover = test_client.put(
        "/api/users/me/cover", json={"data": _b64(JPEG), "content_type": "image/jpeg"}, headers=writer["headers"]
    ).json()
    assert cover["cover_url"].startswith(f"/api/users/{uid}/cover?v=")
    assert test_client.get(f"/api/users/{uid}/cover").content == JPEG

    # The photo shows up wherever the writer is shown, e.g. as a story's author
    post = _create_post(test_client, writer["headers"], "तस्वीर वाला लेखक")
    assert test_client.get(f"/api/posts/{post['id']}").json()["author"]["avatar_url"] == url

    removed = test_client.delete("/api/users/me/avatar", headers=writer["headers"]).json()
    assert removed["avatar_url"] is None
    assert test_client.get(f"/api/users/{uid}/avatar").status_code == 404
    assert test_client.delete("/api/users/me/cover", headers=writer["headers"]).json()["cover_url"] is None


def test_images_are_checked(test_client):
    writer = _register_and_login(test_client)
    put = lambda body: test_client.put("/api/users/me/avatar", json=body, headers=writer["headers"])
    assert put({"data": _b64(JPEG), "content_type": "image/png"}).status_code == 400  # not really a PNG
    assert put({"data": "not base64!!", "content_type": "image/png"}).status_code == 400
    assert put({"data": _b64(b"GIF89a" + b"0" * 50), "content_type": "image/gif"}).status_code == 400  # animated: no
    assert put({"data": _b64(PNG), "content_type": "text/html"}).status_code == 400
    big = PNG + b"0" * (301 * 1024)
    assert put({"data": _b64(big), "content_type": "image/png"}).status_code == 413


def test_pins_are_own_published_stories_only(test_client, writer_b):
    writer = _register_and_login(test_client)
    mine = _create_post(test_client, writer["headers"], "चुनी हुई रचना")
    draft = _create_post(test_client, writer["headers"], "मसौदा", published=0)
    theirs = _create_post(test_client, writer_b["headers"], "किसी और की")

    assert _save(test_client, writer, pinned_post_ids=[theirs["id"]]).status_code == 400
    assert _save(test_client, writer, pinned_post_ids=[draft["id"]]).status_code == 400
    assert _save(test_client, writer, pinned_post_ids=[99999999]).status_code == 400
    ok = _save(test_client, writer, pinned_post_ids=[mine["id"]])
    assert ok.status_code == 200 and ok.json()["pinned_post_ids"] == [mine["id"]]

    # Unpublishing a pinned story quietly drops it from the profile
    test_client.put(f"/api/posts/{mine['id']}", json={"published": 0}, headers=writer["headers"])
    assert _profile(test_client, writer)["pinned_post_ids"] == []


def test_badges_and_counts_come_from_real_numbers(test_client, writer_b):
    fresh = _register_and_login(test_client)
    assert _profile(test_client, fresh)["badges"] == []

    post = _create_post(test_client, fresh["headers"], "पहली रचना")
    body = _profile(test_client, fresh)
    assert body["post_count"] == 1
    assert [b["key"] for b in body["badges"]] == ["stories-1"]

    for _ in range(3):
        test_client.post(f"/api/posts/{post['id']}/clap", headers=writer_b["headers"])
    test_client.post(f"/api/follows/{fresh['user']['id']}", headers=writer_b["headers"])
    body = _profile(test_client, fresh, viewer=writer_b)
    assert body["claps_total"] == 3 and body["follower_count"] == 1 and body["is_following"] is True
    assert [b["key"] for b in body["badges"]] == ["stories-1"]  # 3 claps / 1 follower are below the first tiers


def test_a_profile_read_warns_about_nothing(test_client, writer_b):
    """Badges reach the response as Badge objects, not as dicts.

    ``UserProfileResponse.badges`` is not re-validated when assigned, so plain dicts made
    Pydantic print a serializer warning on every profile read - noise that hides the real
    warnings, and a response built by a fallback path instead of the declared one.
    """
    writer = _register_and_login(test_client)
    _create_post(test_client, writer["headers"], "चेतावनी नहीं वाली रचना")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        body = _profile(test_client, writer, viewer=writer_b)

    assert [b["key"] for b in body["badges"]] == ["stories-1"]
    noisy = [str(w.message) for w in caught if "serializer" in str(w.message)]
    assert not noisy, noisy


def test_the_accent_travels_with_the_author_but_nothing_private_does(test_client):
    writer = _register_and_login(test_client)
    _save(test_client, writer, accent="#1d4ed8")
    post = _create_post(test_client, writer["headers"], "रंगीन लेखक")
    author = test_client.get(f"/api/posts/{post['id']}").json()["author"]
    assert author["accent"] == "#1d4ed8"
    assert "email" not in author
