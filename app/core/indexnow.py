"""IndexNow: push changed URLs to Bing, Yandex and the other participating search engines.

Google does not take part (it finds pages through the sitemap), so this is purely
about getting stories into Bing & co. within minutes instead of days. Runs after
the response is sent (FastAPI background task) and never raises: a search-engine
hiccup must not break publishing.
"""

import logging
from typing import Iterable, List
from urllib.parse import quote, urlparse

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 100  # well under the protocol's 10,000 per request


def story_url(slug: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/post/{quote(slug)}"


def urls_for_story_change(slug: str, author_id: int, tag_slugs: Iterable[str]) -> List[str]:
    """The story itself plus the pages that list it (home, author page, tag pages)."""
    base = settings.FRONTEND_URL.rstrip("/")
    urls = [story_url(slug), f"{base}/", f"{base}/user/{author_id}"]
    urls += [f"{base}/tag/{quote(tag)}" for tag in tag_slugs]
    return urls


def notify(urls: Iterable[str]) -> None:
    """Submit URLs. No-op unless IndexNow is enabled (production)."""
    enabled = settings.INDEXNOW_ENABLED if settings.INDEXNOW_ENABLED is not None else not settings.debug
    if not enabled or not settings.INDEXNOW_KEY:
        return
    base = settings.FRONTEND_URL.rstrip("/")
    unique = list(dict.fromkeys(urls))[:MAX_URLS]
    if not unique:
        return
    try:
        response = requests.post(
            ENDPOINT,
            json={
                "host": urlparse(base).netloc,
                "key": settings.INDEXNOW_KEY,
                "keyLocation": f"{base}/{settings.INDEXNOW_KEY}.txt",
                "urlList": unique,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )
        if response.status_code not in (200, 202):
            logger.warning(f"IndexNow rejected {len(unique)} URLs: HTTP {response.status_code}")
    except requests.RequestException as exc:
        logger.warning(f"IndexNow unreachable: {exc}")
