from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
import json
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from typing import Optional
from app.api import api_router
from app.core.config import settings
from app.core.errorlog import describe, record_server_error
from app.migrations import run_migrations

def guard_production_secrets() -> None:
    """Refuse to serve a production API on the sample secret.

    DEBUG now fails closed, so an environment that never set it is treated as production:
    the repo's placeholder key must not become a way to forge anyone's session.
    """
    if not settings.debug and settings.SECRET_KEY.startswith("your-secret-key"):
        raise RuntimeError(
            "SECRET_KEY must be set to a private random value when DEBUG is off "
            "(run locally with: DEBUG=true uvicorn app.main:app --reload)"
        )


guard_production_secrets()

# Bring the schema up to date (creates tables, adds columns/indexes, backfills) before serving.
run_migrations()

# Interactive API docs are a dev convenience; don't publish the schema in production.
app = FastAPI(
    title=settings.app_name,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)

# Largest request body we accept (a post with an inline cover image is ~4 MB).
MAX_BODY_BYTES = 6 * 1024 * 1024

_OVERSIZE_BODY = json.dumps({"detail": "अनुरोध बहुत बड़ा है"}, ensure_ascii=False).encode("utf-8")


def _declared_length(scope) -> Optional[int]:
    for key, value in scope.get("headers", ()):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodyLimitMiddleware:
    """Refuse a request whose body turns out to be larger than we accept.

    The content-length header alone is not a limit: a caller can declare a small body and
    stream whatever it likes after that, or send chunks with no length at all. So every
    byte that arrives is counted here. Past the cap the rest is dropped, the endpoint is
    handed an empty body, and whatever it answers is replaced by the refusal.
    """

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.limit:
            await self._refuse(send)
            return

        state = {"read": 0, "over": False, "started": False, "finished": False}

        async def counted_receive():
            message = await receive()
            if message["type"] != "http.request":
                return message
            if state["over"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            state["read"] += len(message.get("body", b""))
            if state["read"] > self.limit:
                state["over"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message):
            kind = message["type"]
            if not state["over"]:
                await send(message)
                return
            if kind == "http.response.start" and not state["started"]:
                state["started"] = True
                await send(_oversize_start())
            elif kind == "http.response.body" and not state["finished"]:
                state["finished"] = True
                await send({"type": "http.response.body", "body": _OVERSIZE_BODY, "more_body": False})
            # Everything the endpoint streamed after that is dropped.

        await self.app(scope, counted_receive, guarded_send)

    @staticmethod
    async def _refuse(send) -> None:
        await send(_oversize_start())
        await send({"type": "http.response.body", "body": _OVERSIZE_BODY, "more_body": False})


def _oversize_start() -> dict:
    return {
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(_OVERSIZE_BODY)).encode("ascii")),
        ],
    }


@app.middleware("http")
async def remember_server_errors(request: Request, call_next):
    """Keep a short log of 5xx answers for the owner's status page."""
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 - re-raised below; the server still answers 500
        await run_in_threadpool(record_server_error, request.method, request.url.path, 500, describe(exc))
        raise
    if response.status_code >= 500:
        await run_in_threadpool(record_server_error, request.method, request.url.path, response.status_code, None)
    return response


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    # A unique/foreign-key race that slipped past the checks: a clean 409, not a 500.
    return JSONResponse(
        status_code=409,
        content={"detail": "यह जानकारी पहले से मौजूद है या बदल चुकी है, कृपया दोबारा कोशिश करें"},
    )

# CORS: our own frontend, this project's Vercel previews, and local dev when DEBUG is on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL.rstrip("/")] + (settings.ALLOW_ORIGINS if settings.debug else []),
    allow_origin_regex=r"https://lekhanii(-[a-z0-9-]+)?\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Starlette rejects a preflight carrying Access-Control-Request-Private-Network with
    # "400 Disallowed CORS private-network" unless this is said out loud, and it answers
    # preflights itself without ever calling the middleware below. Without the flag the
    # browser is told "no" before a page on a public origin can reach this server on a LAN.
    allow_private_network=True,
)


class ExtraResponseHeaders(BaseHTTPMiddleware):
    """The headers CORSMiddleware will not set for us.

    ``Cross-Origin-Opener-Policy: same-origin-allow-popups`` keeps the Google sign-in popup
    working while still isolating this page. ``Access-Control-Allow-Private-Network`` is set on
    any *non*-preflight response that carried the marker: CORSMiddleware answers the preflight
    itself (see ``allow_private_network`` above), so this only covers the rarer case of a request
    that sends the marker without being an OPTIONS preflight.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        if request.headers.get("access-control-request-private-network"):
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


# Registered as ASGI middlewares so they run outside/around CORSMiddleware and see its responses.
app.add_middleware(ExtraResponseHeaders)
# Outermost: an oversized body is refused before anything else spends work on it.
app.add_middleware(BodyLimitMiddleware)

app.include_router(api_router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "लेखनी — हिंदी लेखकों का अपना मंच", "name": "लेखनी"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)