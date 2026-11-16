"""Which websites' pages may call the API from a browser: the site itself and what the settings add, and
nobody else (in particular not any "lekhanii-something" project that a stranger makes on Vercel)."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import guard_cors_config, install_cors

STRANGER = "https://lekhanii-evil.vercel.app"


def allowed(client, origin) -> bool:
    """Whether the browser would be let through: the answer names this origin."""
    answer = client.get("/api/posts?limit=1", headers={"Origin": origin})
    return answer.headers.get("access-control-allow-origin") == origin


def preflight_status(client, origin) -> int:
    return client.options(
        "/api/posts", headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    ).status_code


def test_the_site_itself_is_allowed_and_a_look_alike_vercel_project_is_not(test_client):
    own = settings.FRONTEND_URL.rstrip("/")
    assert allowed(test_client, own) and preflight_status(test_client, own) == 200
    assert not allowed(test_client, STRANGER)
    assert preflight_status(test_client, STRANGER) == 400  # the browser is told no before it sends anything
    for other in ("https://lekhanii.vercel.app.evil.example", "https://evil.example", "http://lekhanii.vercel.app"):
        if other != own:
            assert not allowed(test_client, other), other


def test_local_dev_addresses_are_allowed_only_while_debug_is_on(test_client):
    assert settings.debug and allowed(test_client, "http://localhost:3000")


def throwaway_app(**overrides) -> TestClient:
    """A tiny app with the real CORS rule built from the given settings (the real app reads them once, at start)."""
    with patch.multiple(settings, **overrides):
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        install_cors(app)
        return TestClient(app)


def seen(client, origin) -> bool:
    return client.get("/ping", headers={"Origin": origin}).headers.get("access-control-allow-origin") == origin


def test_the_site_follows_frontend_url_when_the_domain_changes():
    client = throwaway_app(FRONTEND_URL="https://lekhani.example/", CORS_EXTRA_ORIGINS="", CORS_ORIGIN_REGEX="", debug=False)
    assert seen(client, "https://lekhani.example")  # a trailing slash in the setting does not matter
    assert not seen(client, "https://lekhanii.vercel.app")  # the old address is no longer on the list
    assert not seen(client, STRANGER)


def test_the_old_address_can_be_kept_for_a_while_through_the_extra_origins():
    client = throwaway_app(
        FRONTEND_URL="https://lekhani.example", CORS_EXTRA_ORIGINS=" https://lekhanii.vercel.app/ , https://www.lekhani.example ",
        CORS_ORIGIN_REGEX="", debug=False,
    )
    assert seen(client, "https://lekhani.example") and seen(client, "https://lekhanii.vercel.app")
    assert seen(client, "https://www.lekhani.example")
    assert not seen(client, STRANGER)


def test_test_links_work_only_for_the_pattern_that_names_your_own_account():
    client = throwaway_app(
        FRONTEND_URL="https://lekhani.example", CORS_EXTRA_ORIGINS="",
        CORS_ORIGIN_REGEX=r"https://lekhanii-[a-z0-9-]+-myaccount\.vercel\.app", debug=False,
    )
    assert seen(client, "https://lekhanii-git-fix-myaccount.vercel.app")
    assert not seen(client, "https://lekhanii-git-fix-someoneelse.vercel.app")
    assert not seen(client, STRANGER)


def guard(**overrides):
    with patch.multiple(settings, **overrides):
        guard_cors_config()


def test_the_guard_accepts_the_sensible_settings():
    guard(CORS_EXTRA_ORIGINS="", CORS_ORIGIN_REGEX="")
    guard(CORS_EXTRA_ORIGINS="https://lekhanii.vercel.app, https://www.lekhani.example:8443", CORS_ORIGIN_REGEX="")
    guard(CORS_EXTRA_ORIGINS="", CORS_ORIGIN_REGEX=r"https://lekhanii-[a-z0-9-]+-myaccount\.vercel\.app")


@pytest.mark.parametrize("extra", ["*", "lekhani.example", "https://*.example.com", "https://a.example/path", "ftp://a.example"])
def test_the_guard_refuses_extra_origins_that_are_not_exact_addresses(extra):
    with pytest.raises(RuntimeError, match="CORS_EXTRA_ORIGINS"):
        guard(CORS_EXTRA_ORIGINS=extra, CORS_ORIGIN_REGEX="")


@pytest.mark.parametrize(
    "pattern",
    [".*", r"https://.*", r"https://.*\.vercel\.app", r"https://lekhanii(-[a-z0-9-]+)?\.vercel\.app"],  # the last is the old rule
)
def test_the_guard_refuses_a_pattern_that_lets_strangers_in(pattern):
    with pytest.raises(RuntimeError, match="too wide"):
        guard(CORS_EXTRA_ORIGINS="", CORS_ORIGIN_REGEX=pattern)


def test_the_guard_refuses_a_pattern_that_is_not_a_pattern():
    with pytest.raises(RuntimeError, match="not a valid pattern"):
        guard(CORS_EXTRA_ORIGINS="", CORS_ORIGIN_REGEX="https://(unclosed")
