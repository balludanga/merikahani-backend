"""Regression tests for the November 2026 security audit.

Each test names the weakness it keeps closed, so a future change that reopens it fails loudly.
"""

import secrets
import uuid
from datetime import timedelta

import jwt as pyjwt
import pytest
from starlette.requests import Request

from app.api.endpoints.posts import public_base_url
from app.core.config import settings
from app.core.clock import utcnow
from app.core.sanitize import clean_html
from app.core.security import create_access_token


# --- iframe allowlist: a browser and Python must agree on which host a URL means ---------------


@pytest.mark.parametrize(
    "src",
    [
        r"https://evil.example\@youtube.com/embed/x",  # python: youtube.com, browser: evil.example
        r"https://evil.example\.youtube.com/embed/x",
        r"https://evil.example\.youtube-nocookie.com/embed/x",
        r"https://evil.example\@player.vimeo.com/video/1",
        "https://evil.example%5C@youtube.com/embed/x",
        "https://user:pw@www.youtube.com/embed/x",  # user information
        "https://@www.youtube.com/embed/x",
        "https://www.youtube.com:8443/embed/x",  # unusual port
        "https://www.youtube.com\t/embed/x",  # control characters are stripped by browsers
        "https://www.youtube.com /embed/x",
        "https://www.youtube.com./embed/x",
    ],
)
def test_iframe_allowlist_is_not_fooled_by_how_browsers_read_urls(src):
    assert "src=" not in (clean_html(f'<iframe src="{src}"></iframe>') or ""), src


@pytest.mark.parametrize(
    "src",
    [
        "https://www.youtube.com/embed/abc123",
        "https://www.youtube.com/embed/abc123?rel=0&start=10",
        "https://www.youtube-nocookie.com/embed/abc123",
        "https://youtu.be/abc123",
        "https://player.vimeo.com/video/456",
        "https://player.vimeo.com:443/video/456",
    ],
)
def test_real_video_embeds_still_work(src):
    assert f'src="{src}' in (clean_html(f'<iframe src="{src}" width="640" height="360"></iframe>') or "").replace("&amp;", "&")


# --- the signing key must not be a placeholder or guessable -------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "your_secret_key_here",  # the placeholder in .env.example
        "your-secret-key-here-change-in-production",
        "YOUR_SECRET_KEY_HERE",
        "changeme-changeme-changeme-changeme-x",
        "abc",
        "",
        "x" * 31,  # one short of the minimum
    ],
)
def test_production_refuses_placeholder_and_short_secret_keys(monkeypatch, key):
    from app.main import guard_production_secrets

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        guard_production_secrets()


@pytest.mark.parametrize("key", [secrets.token_urlsafe(48), secrets.token_urlsafe(32), "k" * 32])
def test_production_accepts_a_long_random_secret_key(monkeypatch, key):
    from app.main import guard_production_secrets

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "SECRET_KEY", key)
    guard_production_secrets()


# --- login tokens: the library was swapped, so prove forgeries still fail -----------------------


def _token(**claims):
    return pyjwt.encode(claims, settings.SECRET_KEY, algorithm="HS256")


def _status(test_client, token):
    return test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def test_a_genuine_token_works_and_forgeries_do_not(test_client, writer_a):
    uid = writer_a["user"]["id"]
    far = utcnow() + timedelta(minutes=5)
    assert _status(test_client, create_access_token({"sub": str(uid), "tv": 0}, timedelta(minutes=5))) == 200

    # expired
    expired = _token(sub=str(uid), tv=0, exp=utcnow() - timedelta(minutes=1))
    response = test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401 and "समाप्त" in response.json()["detail"]

    # signed with another key, tampered, unsigned, or with a different HMAC algorithm
    assert _status(test_client, pyjwt.encode({"sub": str(uid), "tv": 0, "exp": far}, "another-key-" * 4, algorithm="HS256")) == 401
    good = create_access_token({"sub": str(uid), "tv": 0}, timedelta(minutes=5))
    assert _status(test_client, good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")) == 401
    assert _status(test_client, pyjwt.encode({"sub": str(uid), "tv": 0, "exp": far}, None, algorithm="none")) == 401
    assert _status(test_client, pyjwt.encode({"sub": str(uid), "tv": 0, "exp": far}, settings.SECRET_KEY, algorithm="HS512")) == 401

    # the wrong kind of claim, a retired session, a user that does not exist, plain garbage
    assert _status(test_client, _token(sub="not-a-number", tv=0, exp=far)) == 401
    assert _status(test_client, _token(sub=str(uid), tv=99, exp=far)) == 401
    assert _status(test_client, _token(sub="99999999", tv=0, exp=far)) == 401
    assert _status(test_client, _token(tv=0, exp=far)) == 401
    assert _status(test_client, "not.a.token") == 401


# --- links built from a forged Host header ------------------------------------------------------


def _request(host, forwarded_host=None, proto=None):
    headers = [(b"host", host.encode())]
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))
    if proto:
        headers.append((b"x-forwarded-proto", proto.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "scheme": "http",
                    "server": ("testserver", 80), "headers": headers})


def test_links_use_the_sites_own_address_not_a_forged_host(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://lekhanii.vercel.app")
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "lekhanii-backend.onrender.com")

    site = "https://lekhanii.vercel.app"
    assert public_base_url(_request("evil.example", proto="https")) == site
    assert public_base_url(_request("lekhanii-backend.onrender.com", forwarded_host="evil.example", proto="https")) == site
    assert public_base_url(_request("lekhanii.vercel.app", proto="javascript")) == site
    # the real addresses keep working
    assert public_base_url(_request("lekhanii.vercel.app", proto="https")) == site
    assert public_base_url(_request("lekhanii-backend.onrender.com", proto="https")) == "https://lekhanii-backend.onrender.com"

    # development trusts the request: the API runs on its own port there
    monkeypatch.setattr(settings, "debug", True)
    assert public_base_url(_request("localhost:8001")) == "http://localhost:8001"


# --- the cover address must not be a signpost to other sites ------------------------------------


def test_cover_redirect_only_leads_into_our_own_image_storage(test_client, writer_a, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co")
    own = "https://proj.supabase.co/storage/v1/object/public/lekhani-media/media/1/a.png"

    def story(cover):
        response = test_client.post(
            "/api/posts",
            json={"title": f"आवरण {uuid.uuid4().hex[:6]}", "content": "पाठ", "published": 1, "cover_image": cover},
            headers=writer_a["headers"],
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    foreign = story("https://evil.example/phish.png")
    lookalike = story("https://proj.supabase.co.evil.example/a.png")
    insecure = story("http://proj.supabase.co/a.png")
    mine = story(own)

    for post_id in (foreign, lookalike, insecure):
        assert test_client.get(f"/api/posts/{post_id}/cover", follow_redirects=False).status_code == 404
    redirect = test_client.get(f"/api/posts/{mine}/cover", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == own


# --- CORS: sign-in travels in a header, so credentials mode is never needed --------------------


def test_cors_does_not_allow_credentials_but_still_allows_the_sign_in_header(test_client):
    origin = settings.FRONTEND_URL.rstrip("/")
    preflight = test_client.options(
        "/api/posts",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in preflight.headers
    plain = test_client.get("/api/posts", headers={"Origin": origin})
    assert "access-control-allow-credentials" not in plain.headers
