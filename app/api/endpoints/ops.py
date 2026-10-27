"""The owner's status page: how big the database is, who is joining, what is breaking.

Only an account whose email is listed in ADMIN_EMAILS *and* was verified by Google may read
this. Everyone else, signed in or not, gets the same 404 a made-up address gets, so the
page does not reveal that it exists.
"""

import os
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.clock import from_timestamp, utcnow
from app.core.config import settings
from app.core.dependencies import get_current_user_optional
from app.database import engine, get_db
from app.models.bookmark import Bookmark
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.follow import Follow
from app.models.highlight import Highlight
from app.models.notification import Notification
from app.models.post import Post
from app.models.server_error import ServerError
from app.models.user import User

router = APIRouter()

STARTED_AT = time.time()
# Supabase's free plan allows 500 MB of database.
DATABASE_LIMIT_BYTES = 500 * 1024 * 1024
DAYS = 30


def admin_emails() -> set:
    return {item.strip().lower() for item in (settings.ADMIN_EMAILS or "").split(",") if item.strip()}


def is_admin(user: Optional[User]) -> bool:
    return bool(user and user.email_verified and (user.email or "").strip().lower() in admin_emails())


def require_admin(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not is_admin(user):
        raise HTTPException(status_code=404, detail="Not Found")
    return user


def _per_day(db: Session, column, since: datetime, *filters) -> dict:
    """{ 'YYYY-MM-DD': count } of rows created since ``since``."""
    day = func.date(column)
    rows = db.query(day, func.count()).filter(column >= since, *filters).group_by(day).all()
    return {str(key)[:10]: count for key, count in rows}


def _database() -> dict:
    kind = engine.dialect.name
    if kind == "postgresql":
        with engine.connect() as conn:
            size = conn.execute(text("SELECT pg_database_size(current_database())")).scalar()
            tables = conn.execute(
                text(
                    "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 2 DESC LIMIT 6"
                )
            ).all()
        return {
            "kind": kind,
            "size_bytes": int(size or 0),
            "limit_bytes": DATABASE_LIMIT_BYTES,
            "tables": [{"name": name, "size_bytes": int(size)} for name, size in tables],
        }
    path = engine.url.database
    size = os.path.getsize(path) if path and os.path.exists(path) else 0
    return {"kind": kind, "size_bytes": size, "limit_bytes": None, "tables": []}


@router.get("/overview")
def overview(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    now = utcnow()
    since = (now - timedelta(days=DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)

    signups = _per_day(db, User.created_at, since)
    stories = _per_day(db, Post.created_at, since, Post.published == 1)
    responses = _per_day(db, Comment.created_at, since)
    daily = []
    for offset in range(DAYS):
        key = (since + timedelta(days=offset)).strftime("%Y-%m-%d")
        daily.append(
            {"date": key, "signups": signups.get(key, 0), "stories": stories.get(key, 0), "responses": responses.get(key, 0)}
        )

    def count(model, *filters) -> int:
        return db.query(func.count(model.id)).filter(*filters).scalar() or 0

    story_counts = dict(
        db.query(Post.author_id, func.count(Post.id)).filter(Post.published == 1).group_by(Post.author_id).all()
    )
    recent = db.query(User).order_by(User.created_at.desc(), User.id.desc()).limit(10).all()
    top = (
        db.query(Post)
        .filter(Post.published == 1)
        .order_by(Post.clap_count.desc(), Post.id.desc())
        .limit(5)
        .all()
    )
    day_ago = now - timedelta(days=1)
    latest_errors = db.query(ServerError).order_by(ServerError.id.desc()).limit(15).all()

    return {
        "generated_at": now.isoformat(),
        "server": {
            "started_at": from_timestamp(STARTED_AT).isoformat(),
            "uptime_seconds": int(time.time() - STARTED_AT),
            "commit": (os.getenv("RENDER_GIT_COMMIT") or "")[:7] or None,
            "debug": bool(settings.debug),
        },
        "database": _database(),
        "totals": {
            "users": count(User),
            "google_users": count(User, User.auth_provider == "google"),
            "stories": count(Post, Post.published == 1),
            "drafts": count(Post, Post.published != 1),
            "responses": count(Comment),
            "highlights": count(Highlight),
            "claps": db.query(func.coalesce(func.sum(Clap.count), 0)).scalar() or 0,
            "bookmarks": count(Bookmark),
            "follows": count(Follow),
            "notifications": count(Notification),
        },
        "recent": {
            "signups_7d": count(User, User.created_at >= week),
            "stories_7d": count(Post, Post.published == 1, Post.created_at >= week),
            "responses_7d": count(Comment, Comment.created_at >= week),
        },
        "daily": daily,
        "recent_users": [
            {
                "id": user.id,
                "username": user.username,
                "provider": user.auth_provider,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "stories": story_counts.get(user.id, 0),
            }
            for user in recent
        ],
        "top_stories": [
            {"id": post.id, "title": post.title, "slug": post.slug, "claps": post.clap_count or 0}
            for post in top
        ],
        "errors": {
            "last_24h": count(ServerError, ServerError.created_at >= day_ago),
            "last_7d": count(ServerError, ServerError.created_at >= week),
            "latest": [
                {
                    "id": row.id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "method": row.method,
                    "path": row.path,
                    "status": row.status,
                    "error": row.error,
                }
                for row in latest_errors
            ],
        },
    }
