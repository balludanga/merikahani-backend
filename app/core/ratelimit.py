"""Small in-memory sliding-window rate limiter for the auth endpoints.

State lives in this process only. That is enough for the single free-tier
Render instance; if the API ever runs on several instances, move the
counters to a shared store (Redis / the database).
"""

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, window: int, now: float) -> Deque[float]:
        events = self._events[key]
        while events and now - events[0] > window:
            events.popleft()
        if not events:
            self._events.pop(key, None)
        return events

    def count(self, key: str, window: int) -> int:
        with self._lock:
            return len(self._prune(key, window, time.monotonic()))

    def retry_after(self, key: str, window: int) -> int:
        with self._lock:
            now = time.monotonic()
            events = self._prune(key, window, now)
            return max(1, int(window - (now - events[0]))) if events else 1

    def record(self, key: str) -> None:
        with self._lock:
            self._events[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


# Every request from one address (generous: shared NATs and proxies exist).
ip_attempts = SlidingWindowLimiter()
# Failed password logins per account — slows down password guessing.
login_failures = SlidingWindowLimiter()
# New accounts per address.
registrations = SlidingWindowLimiter()
# Per-account write actions (posts, comments, claps, uploads).
actions = SlidingWindowLimiter()

LOGIN_WINDOW = 15 * 60
LOGIN_IP_LIMIT = 60
LOGIN_FAILURE_LIMIT = 8
REGISTER_WINDOW = 60 * 60
REGISTER_IP_LIMIT = 10


def client_ip(request: Request) -> str:
    """Caller address; the API sits behind Cloudflare, which sets these headers."""
    # TEMPORARY probe: which address headers reach us behind Vercel + Render? Remove after reading the logs.
    logger.warning(
        "ip-probe cf=%r xff=%r real=%r peer=%r",
        request.headers.get("cf-connecting-ip"),
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        request.client.host if request.client else None,
    )
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def too_many(limiter: SlidingWindowLimiter, key: str, window: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="बहुत अधिक प्रयास हो गए हैं। कृपया कुछ देर बाद फिर कोशिश करें।",
        headers={"Retry-After": str(limiter.retry_after(key, window))},
    )


def enforce(limiter: SlidingWindowLimiter, key: str, limit: int, window: int, *, record: bool) -> None:
    """Raise 429 when ``key`` already has ``limit`` events in the window."""
    if limiter.count(key, window) >= limit:
        raise too_many(limiter, key, window)
    if record:
        limiter.record(key)
