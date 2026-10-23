import uuid

from app.core.sanitize import clean_html


def _draft(client, headers, **extra):
    body = {"title": f"गुप्त {uuid.uuid4().hex[:6]}", "content": "निजी मसौदा", "published": 0, **extra}
    response = client.post("/api/posts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_drafts_are_private(test_client, writer_a, writer_b):
    draft = _draft(test_client, writer_a["headers"])
    pid, slug, author_id = draft["id"], draft["slug"], draft["author_id"]

    # Anonymous and other signed-in users cannot read it by id or slug.
    for headers in ({}, writer_b["headers"]):
        assert test_client.get(f"/api/posts/{pid}", headers=headers).status_code == 404
        assert test_client.get(f"/api/posts/slug/{slug}", headers=headers).status_code == 404
        assert test_client.get(f"/api/comments/post/{pid}", headers=headers).status_code == 404
        listed = test_client.get("/api/posts?published=0", headers=headers).json()
        assert all(p["id"] != pid for p in listed)
        by_user = test_client.get(f"/api/posts/user/{author_id}?published=0", headers=headers).json()
        assert all(p["id"] != pid for p in by_user)
    for action in (
        lambda: test_client.post(f"/api/bookmarks/{pid}", headers=writer_b["headers"]),
        lambda: test_client.post(f"/api/posts/{pid}/clap", headers=writer_b["headers"]),
        lambda: test_client.post(
            "/api/comments", json={"post_id": pid, "content": "हाय"}, headers=writer_b["headers"]
        ),
        lambda: test_client.post(
            "/api/highlights", json={"post_id": pid, "text": "निजी"}, headers=writer_b["headers"]
        ),
    ):
        assert action().status_code == 404

    # The author still sees it everywhere.
    assert test_client.get(f"/api/posts/{pid}", headers=writer_a["headers"]).status_code == 200
    assert test_client.get(f"/api/posts/slug/{slug}", headers=writer_a["headers"]).status_code == 200
    own = test_client.get("/api/posts?published=0", headers=writer_a["headers"]).json()
    assert any(p["id"] == pid for p in own)
    mine = test_client.get("/api/posts/me?published=0", headers=writer_a["headers"]).json()
    assert any(p["id"] == pid for p in mine)


def test_unpublished_bookmark_disappears_from_bookmarks(test_client, writer_a, writer_b):
    post = test_client.post(
        "/api/posts",
        json={"title": f"खुली {uuid.uuid4().hex[:6]}", "content": "सबके लिए", "published": 1},
        headers=writer_a["headers"],
    ).json()
    assert test_client.post(f"/api/bookmarks/{post['id']}", headers=writer_b["headers"]).status_code == 201
    test_client.put(f"/api/posts/{post['id']}", json={"published": 0}, headers=writer_a["headers"])
    bookmarks = test_client.get("/api/bookmarks", headers=writer_b["headers"]).json()
    assert all(p["id"] != post["id"] for p in bookmarks)


def test_public_responses_never_include_email(test_client, writer_a):
    post = test_client.post(
        "/api/posts",
        json={"title": f"खुली {uuid.uuid4().hex[:6]}", "content": "पाठ", "published": 1},
        headers=writer_a["headers"],
    ).json()
    email = writer_a["user"]["email"]
    uid = writer_a["user"]["id"]
    for url in (
        "/api/posts",
        f"/api/posts/{post['id']}",
        f"/api/posts/user/{uid}",
        f"/api/users/{uid}",
        f"/api/follows/{uid}/followers",
        "/api/rss.xml",
    ):
        response = test_client.get(url)
        assert response.status_code == 200, url
        assert email not in response.text, url
    # The owner's own account endpoints still carry it.
    assert test_client.get("/api/auth/me", headers=writer_a["headers"]).json()["email"] == email


def test_login_locks_after_repeated_failures(test_client, writer_a):
    email = writer_a["user"]["email"]
    for _ in range(8):
        assert test_client.post("/api/auth/login", json={"email": email, "password": "galat-pass"}).status_code == 401
    blocked = test_client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_successful_login_clears_failures(test_client, writer_a):
    email = writer_a["user"]["email"]
    for _ in range(3):
        test_client.post("/api/auth/login", json={"email": email, "password": "galat-pass"})
    assert test_client.post("/api/auth/login", json={"email": email, "password": "secret123"}).status_code == 200


def test_register_is_rate_limited_and_password_policy(test_client):
    short = test_client.post(
        "/api/auth/register",
        json={"email": "s@example.com", "username": "shortpw", "password": "abc"},
    )
    assert short.status_code == 422
    for i in range(10):
        test_client.post(
            "/api/auth/register",
            json={"email": f"r{i}_{uuid.uuid4().hex[:6]}@example.com", "username": f"r{uuid.uuid4().hex[:8]}", "password": "secret123"},
        )
    over = test_client.post(
        "/api/auth/register",
        json={"email": "x@example.com", "username": "overlimit", "password": "secret123"},
    )
    assert over.status_code == 429


def _register_from(test_client, visitor, edge, tag):
    return test_client.post(
        "/api/auth/register",
        json={"email": f"{tag}_{uuid.uuid4().hex[:8]}@example.com", "username": f"u{uuid.uuid4().hex[:10]}", "password": "secret123"},
        headers={"x-forwarded-for": f"{visitor}, {edge}, 172.69.0.1", "cf-connecting-ip": edge},
    )


def test_visitors_behind_the_shared_edge_address_are_limited_separately(test_client):
    """Through Vercel every visitor shares one cf-connecting-ip; only x-forwarded-for tells them apart."""
    vercel = "13.235.27.61"
    for _ in range(10):
        assert _register_from(test_client, "106.219.120.96", vercel, "a").status_code == 201
    assert _register_from(test_client, "106.219.120.96", vercel, "a").status_code == 429
    # A different visitor through the same Vercel address is not blocked by the first one's burst.
    assert _register_from(test_client, "49.36.1.2", vercel, "b").status_code == 201


def test_spoofed_forwarded_for_cannot_dodge_the_limit(test_client):
    """A direct caller rotates fake x-forwarded-for values but cannot change cf-connecting-ip."""
    from app.core.ratelimit import EDGE_LIMIT_FACTOR, REGISTER_IP_LIMIT

    allowed = REGISTER_IP_LIMIT * EDGE_LIMIT_FACTOR
    # Fill the edge allowance directly instead of making hundreds of bcrypt registrations.
    from app.core import ratelimit
    for _ in range(allowed):
        ratelimit.registrations.record("edge:203.0.113.7")
    blocked = _register_from(test_client, "9.9.9.9", "203.0.113.7", "s")
    assert blocked.status_code == 429


def test_client_ip_prefers_forwarded_for_and_falls_back_to_the_edge_address():
    from starlette.requests import Request
    from app.core.ratelimit import client_ip, edge_ip

    def req(headers, peer=("10.0.0.1", 1)):
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "client": peer})

    both = req({"x-forwarded-for": "106.219.120.96, 13.235.27.61, 10.1.1.1", "cf-connecting-ip": "13.235.27.61"})
    assert client_ip(both) == "106.219.120.96"
    assert edge_ip(both) == "13.235.27.61"
    assert client_ip(req({"cf-connecting-ip": "1.2.3.4"})) == "1.2.3.4"
    assert client_ip(req({})) == "10.0.0.1"
    assert edge_ip(req({}, peer=None)) == "unknown"


def test_google_accounts_cannot_log_in_with_a_password(test_client):
    google = test_client.post("/api/auth/google-login", json={"token": "a" * 80})
    assert google.status_code == 200, google.text
    email = google.json()["user"]["email"]
    # Old behaviour hashed the first 72 chars of the Google token as the password.
    assert test_client.post("/api/auth/login", json={"email": email, "password": "a" * 72}).status_code == 401


def test_google_login_rejects_unverified_email(test_client):
    from unittest.mock import patch

    with patch("app.api.endpoints.auth.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "email": "unverified@example.com",
            "aud": "test-google-client-id",
            "email_verified": "false",
        }
        response = test_client.post("/api/auth/google-login", json={"token": "t" * 40})
    assert response.status_code == 401


def test_iframe_host_must_match_exactly():
    bad = [
        '<iframe src="https://evil.example/?youtube.com"></iframe>',
        '<iframe src="//evil.example/x/youtube.com"></iframe>',
        '<iframe src="https://youtube.com.evil.example/embed/x"></iframe>',
        '<iframe src="http://www.youtube.com/embed/x"></iframe>',
    ]
    for html in bad:
        assert "src=" not in (clean_html(html) or ""), html
    good = clean_html('<iframe src="https://www.youtube.com/embed/abc" allow="autoplay; camera"></iframe>')
    assert 'src="https://www.youtube.com/embed/abc"' in good
    assert "camera" not in good


def test_deleting_a_post_with_highlights_works(test_client, writer_a, writer_b):
    post = test_client.post(
        "/api/posts",
        json={"title": f"हटेगी {uuid.uuid4().hex[:6]}", "content": "कुछ पाठ", "published": 1},
        headers=writer_a["headers"],
    ).json()
    assert test_client.post(
        "/api/highlights", json={"post_id": post["id"], "text": "कुछ"}, headers=writer_b["headers"]
    ).status_code == 201
    assert test_client.post(f"/api/bookmarks/{post['id']}", headers=writer_b["headers"]).status_code == 201
    assert test_client.delete(f"/api/posts/{post['id']}", headers=writer_a["headers"]).status_code == 204


def test_google_signup_gets_unique_username(test_client):
    from unittest.mock import patch

    def sign_in(email):
        with patch("app.api.endpoints.auth.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                "email": email, "aud": "test-google-client-id", "email_verified": "true", "name": "X",
            }
            return test_client.post("/api/auth/google-login", json={"token": "t" * 40})

    tag = uuid.uuid4().hex[:8]
    first = sign_in(f"{tag}@one.example")
    second = sign_in(f"{tag}@two.example")  # same local part -> same wanted username
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["user"]["username"] != second.json()["user"]["username"]


def test_email_case_does_not_create_second_account(test_client):
    tag = uuid.uuid4().hex[:8]
    body = {"email": f"Case_{tag}@Example.com", "username": f"case_{tag}", "password": "secret123"}
    assert test_client.post("/api/auth/register", json=body).status_code == 201
    again = dict(body, email=body["email"].lower(), username=f"other_{tag}")
    assert test_client.post("/api/auth/register", json=again).status_code == 400
    login = test_client.post("/api/auth/login", json={"email": body["email"].upper(), "password": "secret123"})
    assert login.status_code == 200


def test_username_rules(test_client):
    for bad in ("ab", "has space", "a/../b", "x" * 31):
        r = test_client.post(
            "/api/auth/register", json={"email": f"{uuid.uuid4().hex[:6]}@example.com", "username": bad, "password": "secret123"}
        )
        assert r.status_code == 422, bad


def test_post_validation_rejects_bad_input(test_client, writer_a):
    h = writer_a["headers"]
    base = {"title": f"टी {uuid.uuid4().hex[:6]}", "content": "पाठ"}
    assert test_client.post("/api/posts", json=dict(base, published=7), headers=h).status_code == 422
    assert test_client.post("/api/posts", json=dict(base, cover_image="javascript:alert(1)"), headers=h).status_code == 422
    assert test_client.post("/api/posts", json=dict(base, title="क" * 301), headers=h).status_code == 422
    made = test_client.post("/api/posts", json=base, headers=h).json()
    assert test_client.put(f"/api/posts/{made['id']}", json={"title": None}, headers=h).status_code == 422
    assert test_client.put(f"/api/posts/{made['id']}", json={"published": 1}, headers=h).status_code == 200


def test_same_title_in_same_second_does_not_500(test_client, writer_a):
    title = f"एक जैसी {uuid.uuid4().hex[:6]}"
    slugs = set()
    for _ in range(4):
        r = test_client.post("/api/posts", json={"title": title, "content": "पाठ", "published": 1}, headers=writer_a["headers"])
        assert r.status_code == 201, r.text
        slugs.add(r.json()["slug"])
    assert len(slugs) == 4


def test_cover_images_served_as_urls_in_lists(test_client, writer_a):
    import base64
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    cover = f"data:image/png;base64,{png}"
    post = test_client.post(
        "/api/posts",
        json={"title": f"कवर {uuid.uuid4().hex[:6]}", "content": "पाठ", "published": 1, "cover_image": cover},
        headers=writer_a["headers"],
    ).json()
    listed = next(p for p in test_client.get("/api/posts?limit=50").json() if p["id"] == post["id"])
    assert listed["cover_image"].startswith("http") and "/cover" in listed["cover_image"]
    assert listed["content_html"] is None
    image = test_client.get(f"/api/posts/{post['id']}/cover")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert test_client.get(f"/api/posts/{post['id']}/cover", headers={"If-None-Match": image.headers["etag"]}).status_code == 304
    # The author's own view of the story still carries the stored image (see the test below).
    assert test_client.get(f"/api/posts/{post['id']}", headers=writer_a["headers"]).json()["cover_image"] == cover


def test_anonymous_search_cannot_probe_hidden_text(test_client, writer_a):
    marker = uuid.uuid4().hex
    body = ("शुरुआत " * 120) + marker  # marker sits beyond the 600-char teaser
    test_client.post("/api/posts", json={"title": f"खोज {marker[:6]}", "content": body, "published": 1}, headers=writer_a["headers"])
    assert test_client.get(f"/api/posts?q={marker}").json() == []
    assert len(test_client.get(f"/api/posts?q={marker}", headers=writer_a["headers"]).json()) == 1
    # '%' is literal, not a wildcard
    assert test_client.get("/api/posts?q=%25").json() == []


def test_sitemap_share_and_rss_are_well_formed(test_client, writer_a):
    import xml.etree.ElementTree as ET
    title = f"मेरी & कहानी <{uuid.uuid4().hex[:5]}>"
    post = test_client.post("/api/posts", json={"title": title, "content": "पाठ", "published": 1}, headers=writer_a["headers"]).json()
    ET.fromstring(test_client.get("/api/sitemap.xml").text)
    ET.fromstring(test_client.get("/api/rss.xml").text)
    share = test_client.get(f"/api/share/post/{post['slug']}")
    assert share.status_code == 200
    assert 'property="og:title"' in share.text and "<script" not in share.text
    assert "&lt;" in share.text  # title is escaped
    assert test_client.get("/api/share/post/nope-nope").status_code == 404


def test_docs_hidden_when_not_debug():
    from app.main import app
    from app.core.config import settings
    assert (app.docs_url is None) == (not settings.debug)


def test_sanitizer_blocks_protocol_relative_links():
    html = clean_html('<p><a href="//evil.example">x</a> <a href="/\\evil.example">y</a> <a href="/about">ok</a></p>')
    assert "evil.example" not in html and 'href="/about"' in html


def test_old_oversized_rows_are_still_readable(test_client, writer_a):
    """Input limits must not turn already-stored posts into 500s on read."""
    from app.database import SessionLocal
    from app.models.post import Post

    db = SessionLocal()
    try:
        row = Post(
            title="ल" * 400, content="क" * 400_000, slug=f"legacy-{uuid.uuid4().hex[:8]}",
            cover_image="not a url", author_id=writer_a["user"]["id"], published=1,
        )
        db.add(row)
        db.commit()
        pid = row.id
    finally:
        db.close()
    assert test_client.get(f"/api/posts/{pid}", headers=writer_a["headers"]).status_code == 200
    assert test_client.get("/api/posts?limit=100", headers=writer_a["headers"]).status_code == 200


def _google_sign_in(client, email):
    from unittest.mock import patch

    with patch("app.api.endpoints.auth.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "email": email, "aud": "test-google-client-id", "email_verified": "true",
            "name": "असली मालिक", "picture": "http://example.com/a.png",
        }
        return client.post("/api/auth/google-login", json={"token": "t" * 40})


def test_google_sign_in_retires_a_password_registered_by_someone_else(test_client):
    """Pre-registration takeover: attacker registers victim's email, victim then uses Google."""
    tag = uuid.uuid4().hex[:8]
    email = f"victim_{tag}@example.com"
    body = {"email": email, "username": f"evil_{tag}", "password": "attacker-knows-this"}
    assert test_client.post("/api/auth/register", json=body).status_code == 201
    attacker = test_client.post("/api/auth/login", json={"email": email, "password": body["password"]}).json()
    attacker_headers = {"Authorization": f"Bearer {attacker['access_token']}"}
    assert test_client.get("/api/auth/me", headers=attacker_headers).status_code == 200

    owner = _google_sign_in(test_client, email)
    assert owner.status_code == 200
    assert owner.json()["user"]["id"] == attacker["user"]["id"]  # same account, owner keeps it

    # The attacker's password and the session made with it no longer work.
    relogin = test_client.post("/api/auth/login", json={"email": email, "password": body["password"]})
    assert relogin.status_code == 401
    assert "गूगल" in relogin.json()["detail"]
    assert test_client.get("/api/auth/me", headers=attacker_headers).status_code == 401
    owner_headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}
    assert test_client.get("/api/auth/me", headers=owner_headers).status_code == 200


def test_new_google_accounts_are_google_only(test_client):
    from app.database import SessionLocal
    from app.models.user import User

    email = f"g_{uuid.uuid4().hex[:8]}@example.com"
    assert _google_sign_in(test_client, email).status_code == 200
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert (user.auth_provider, user.email_verified) == ("google", True)
    finally:
        db.close()
    assert test_client.post("/api/auth/login", json={"email": email, "password": "x" * 72}).status_code == 401


def test_migrations_upgrade_a_legacy_database_and_retire_weak_google_hashes(tmp_path):
    """The production DB predates Alembic: old columns missing, weak Google hashes present."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from app.migrations import BASE_DIR
    import os

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL, username VARCHAR NOT NULL,
            hashed_password VARCHAR NOT NULL, full_name VARCHAR, bio TEXT, avatar_url VARCHAR,
            created_at DATETIME, updated_at DATETIME)"""))
        conn.execute(text("""CREATE TABLE posts (id INTEGER PRIMARY KEY, title VARCHAR NOT NULL, subtitle VARCHAR,
            content TEXT NOT NULL, slug VARCHAR NOT NULL, cover_image VARCHAR, author_id INTEGER NOT NULL,
            published INTEGER, created_at DATETIME, updated_at DATETIME)"""))
        conn.execute(text("""CREATE TABLE comments (id INTEGER PRIMARY KEY, content TEXT NOT NULL, post_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL, created_at DATETIME, updated_at DATETIME)"""))
        conn.execute(text("""CREATE TABLE claps (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, post_id INTEGER NOT NULL,
            count INTEGER NOT NULL, created_at DATETIME, updated_at DATETIME)"""))
        conn.execute(text("""CREATE TABLE post_tags (post_id INTEGER, tag_id INTEGER)"""))
        conn.execute(text("""CREATE TABLE highlights (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL, text TEXT NOT NULL, created_at DATETIME)"""))
        conn.execute(text("INSERT INTO users (id,email,username,hashed_password,avatar_url) VALUES "
                          "(1,'g@x.com','g','WEAK','https://lh3/a.png'),(2,'p@x.com','p','PWHASH',NULL)"))

    config = Config(os.path.join(BASE_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BASE_DIR, "alembic"))
    for _ in range(2):  # a second run must change nothing
        with engine.connect() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
            conn.commit()

    with engine.connect() as conn:
        rows = {r.id: r for r in conn.execute(text("SELECT id, auth_provider, email_verified, token_version, hashed_password FROM users"))}
        post_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(posts)"))}
        highlight_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(highlights)"))}
        comment_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(comments)"))}
        notification_post_nullable = any(r[1] == "post_id" and r[3] == 0 for r in conn.execute(text("PRAGMA table_info(notifications)")))
        notification_tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        indexes = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))}
    assert (rows[1].auth_provider, rows[1].email_verified, rows[1].token_version) == ("google", 1, 1)
    assert rows[1].hashed_password != "WEAK"
    assert (rows[2].auth_provider, rows[2].email_verified, rows[2].token_version, rows[2].hashed_password) == ("password", 0, 0, "PWHASH")
    assert {"topic", "clap_count", "content_html"} <= post_cols
    assert {"start_offset", "prefix", "suffix"} <= highlight_cols
    assert "quote" in comment_cols
    assert "notifications" in notification_tables
    assert notification_post_nullable
    assert {"ix_posts_published", "ix_posts_created_at", "ix_comments_post_id", "ix_claps_post_id"} <= indexes


def test_readers_get_cover_url_but_the_author_keeps_the_stored_image(test_client, writer_a, writer_b):
    import base64
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"1" * 64).decode()
    cover = f"data:image/png;base64,{png}"
    post = test_client.post(
        "/api/posts",
        json={"title": f"कवर दो {uuid.uuid4().hex[:6]}", "content": "पाठ", "published": 1, "cover_image": cover},
        headers=writer_a["headers"],
    ).json()
    for url in (f"/api/posts/{post['id']}", f"/api/posts/slug/{post['slug']}"):
        anon = test_client.get(url).json()
        assert anon["cover_image"].startswith("http") and anon["cover_image"].count("/cover") == 1
        assert test_client.get(url, headers=writer_b["headers"]).json()["cover_image"].startswith("http")
        assert test_client.get(url, headers=writer_a["headers"]).json()["cover_image"] == cover  # author: raw

    # An editor that echoed the served URL back must not overwrite the real image or loop.
    served = test_client.get(f"/api/posts/{post['id']}").json()["cover_image"]
    saved = test_client.put(f"/api/posts/{post['id']}", json={"cover_image": served}, headers=writer_a["headers"])
    assert saved.status_code == 200 and saved.json()["cover_image"] == cover
    created = test_client.post(
        "/api/posts",
        json={"title": f"कवर तीन {uuid.uuid4().hex[:6]}", "content": "पाठ", "published": 1, "cover_image": served},
        headers=writer_a["headers"],
    ).json()
    assert created["cover_image"] in (None, "")
    assert test_client.get(f"/api/posts/{created['id']}/cover").status_code == 404

    # The image itself is still served.
    assert test_client.get(f"/api/posts/{post['id']}/cover").content.startswith(b"\x89PNG")


def _fake_response(status, body):
    from unittest.mock import Mock

    r = Mock()
    r.status_code = status
    r.json.return_value = body
    return r


def _popup_sign_in(client, tokeninfo, userinfo, userinfo_status=200):
    """Pop-up (access token) sign-in: tokeninfo says who the token was issued to, userinfo who it is."""
    from unittest.mock import patch

    def fake_get(url, **kwargs):
        if "tokeninfo" in url:
            assert kwargs["params"] == {"access_token": "ya29.token"}
            return _fake_response(200, tokeninfo)
        assert "userinfo" in url and kwargs["headers"]["Authorization"] == "Bearer ya29.token"
        return _fake_response(userinfo_status, userinfo)

    with patch("app.api.endpoints.auth.requests.get", side_effect=fake_get):
        return client.post("/api/auth/google-login", json={"access_token": "ya29.token"})


def test_popup_google_sign_in_with_access_token(test_client):
    email = f"popup_{uuid.uuid4().hex[:8]}@example.com"
    ok = _popup_sign_in(
        test_client,
        {"aud": "test-google-client-id", "sub": "123", "email": email, "email_verified": "true"},
        {"sub": "123", "email": email, "email_verified": True, "name": "फ़ोन उपयोगकर्ता", "picture": "http://example.com/p.png"},
    )
    assert ok.status_code == 200, ok.text
    user = ok.json()["user"]
    assert user["full_name"] == "फ़ोन उपयोगकर्ता" and "email" in user
    headers = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert test_client.get("/api/auth/me", headers=headers).json()["email"] == email


def test_popup_sign_in_rejects_a_token_issued_to_another_app(test_client):
    """An access token Google minted for someone else's app must not log anyone in here."""
    email = f"other_{uuid.uuid4().hex[:8]}@example.com"
    stolen = _popup_sign_in(
        test_client,
        {"aud": "some-other-apps-client-id", "sub": "9", "email": email, "email_verified": "true"},
        {"sub": "9", "email": email, "email_verified": True},
    )
    assert stolen.status_code == 401


def test_popup_sign_in_rejects_unverified_email_and_mismatched_identity(test_client):
    email = f"unv_{uuid.uuid4().hex[:8]}@example.com"
    ours = {"aud": "test-google-client-id", "sub": "1", "email": email}
    assert _popup_sign_in(test_client, dict(ours, email_verified="false"), {"sub": "1", "email": email, "email_verified": False}).status_code == 401
    assert _popup_sign_in(test_client, dict(ours, email_verified="true"), {"sub": "2", "email": email, "email_verified": True}).status_code == 401
    assert _popup_sign_in(test_client, dict(ours, email_verified="true"), {}, userinfo_status=401).status_code == 401


def test_google_login_needs_exactly_one_token(test_client):
    assert test_client.post("/api/auth/google-login", json={}).status_code == 422
    assert test_client.post("/api/auth/google-login", json={"token": "a", "access_token": "b"}).status_code == 422
