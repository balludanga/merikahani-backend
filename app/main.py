from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from app.api import api_router
from app.core.config import settings
from app.core.errorlog import describe, record_server_error
from app.migrations import run_migrations

if not settings.debug and settings.SECRET_KEY.startswith("your-secret-key"):
    raise RuntimeError("SECRET_KEY must be set to a private random value when DEBUG is off")

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


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "अनुरोध बहुत बड़ा है"})
    return await call_next(request)


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
)


class CoopHeaderMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        except Exception:
            pass
        return response


class PrivateNetworkMiddleware(BaseHTTPMiddleware):
    """Add `Access-Control-Allow-Private-Network: true` to responses when the
    browser requested a private-network preflight via
    `Access-Control-Request-Private-Network: true`.
    """
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            if request.headers.get("access-control-request-private-network"):
                response.headers["Access-Control-Allow-Private-Network"] = "true"
        except Exception:
            pass
        return response

# Register ASGI middlewares so they run outside/around CORSMiddleware responses
app.add_middleware(CoopHeaderMiddleware)
app.add_middleware(PrivateNetworkMiddleware)

app.include_router(api_router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "लेखनी — हिंदी लेखकों का अपना मंच", "name": "लेखनी"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)