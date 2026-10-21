from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from sqlalchemy import inspect, text
from app.api import api_router
from app.core.config import settings
from app.database import engine, Base
from app import models  # noqa: F401  register ORM models

# Create database tables
Base.metadata.create_all(bind=engine)


def _ensure_post_columns():
    inspector = inspect(engine)
    if "posts" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("posts")}
    statements = []
    if "topic" not in columns:
        statements.append("ALTER TABLE posts ADD COLUMN topic VARCHAR")
    if "clap_count" not in columns:
        statements.append("ALTER TABLE posts ADD COLUMN clap_count INTEGER DEFAULT 0")
    if statements:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))


_ensure_post_columns()

app = FastAPI(title=settings.app_name)

# Update CORS to handle dynamic Vercel URLs
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOW_ORIGINS + [settings.FRONTEND_URL],
    # Allow any Vercel deployment of the frontend (preview + production)
    allow_origin_regex=r"https://.*\.vercel\.app",
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
    return {"message": "लेखनी — हिंदी लेखकों का अपना मंच", "name": "Lekhani"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)