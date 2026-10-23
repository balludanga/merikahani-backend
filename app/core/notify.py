"""Tell a story's author when a reader comments, claps or highlights.

Notifications are secondary: a failure here must never undo or fail the reader's
own action, so ``notify`` swallows and logs its errors.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.notification import CLAP, HIGHLIGHT, Notification
from app.models.post import Post

logger = logging.getLogger(__name__)

EXCERPT = 200
# Claps and highlights from one reader on one story fold into a single unread
# entry, so a reader tapping the clap button 30 times is one notification.
MERGED = (CLAP, HIGHLIGHT)


def _excerpt(text: Optional[str]) -> Optional[str]:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None
    return cleaned if len(cleaned) <= EXCERPT else cleaned[:EXCERPT].rstrip() + "…"


def notify(
    db: Session,
    *,
    post: Post,
    actor_id: int,
    kind: str,
    comment_id: Optional[int] = None,
    text: Optional[str] = None,
    count: Optional[int] = None,
) -> None:
    """Record that ``actor_id`` did ``kind`` on ``post``. Never tells you about yourself.

    ``count`` is the reader's running total (claps); when omitted a merged entry
    just adds one.
    """
    if post.author_id == actor_id:
        return
    try:
        existing = None
        if kind in MERGED:
            existing = (
                db.query(Notification)
                .filter(
                    Notification.user_id == post.author_id,
                    Notification.actor_id == actor_id,
                    Notification.post_id == post.id,
                    Notification.kind == kind,
                    Notification.read_at.is_(None),
                )
                .first()
            )
        if existing is not None:
            existing.count = count if count is not None else (existing.count or 0) + 1
            existing.text = _excerpt(text) or existing.text
            existing.created_at = datetime.utcnow()
        else:
            db.add(
                Notification(
                    user_id=post.author_id,
                    actor_id=actor_id,
                    post_id=post.id,
                    kind=kind,
                    comment_id=comment_id,
                    text=_excerpt(text),
                    count=count if count is not None else 1,
                )
            )
        db.commit()
    except Exception:  # noqa: BLE001 - see module docstring
        db.rollback()
        logger.exception("could not record %s notification for post %s", kind, post.id)
