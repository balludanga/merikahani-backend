"""Tell a story's author when a reader comments, claps or highlights.

Notifications are secondary: a failure here must never undo or fail the reader's
own action, so ``notify`` swallows and logs its errors.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.notification import CLAP, FOLLOW, HIGHLIGHT, Notification
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
    actor_id: int,
    kind: str,
    post: Optional[Post] = None,
    recipient_id: Optional[int] = None,
    comment_id: Optional[int] = None,
    text: Optional[str] = None,
    count: Optional[int] = None,
) -> None:
    """Record that ``actor_id`` did ``kind``. Never tells you about yourself.

    Story events (comment, clap, highlight) go to the story's author, so pass
    ``post``. A follow has no story: pass ``recipient_id``. ``count`` is the
    reader's running total (claps); when omitted a merged entry just adds one.
    """
    recipient_id = post.author_id if post is not None else recipient_id
    if recipient_id is None or recipient_id == actor_id:
        return
    post_id = post.id if post is not None else None
    try:
        existing = None
        if kind in MERGED:
            existing = (
                db.query(Notification)
                .filter(
                    Notification.user_id == recipient_id,
                    Notification.actor_id == actor_id,
                    Notification.post_id == post_id,
                    Notification.kind == kind,
                    Notification.read_at.is_(None),
                )
                .first()
            )
        elif kind == FOLLOW:
            # One "started following you" per person, however often they toggle it.
            if (
                db.query(Notification.id)
                .filter(
                    Notification.user_id == recipient_id,
                    Notification.actor_id == actor_id,
                    Notification.kind == FOLLOW,
                )
                .first()
            ):
                return
        if existing is not None:
            existing.count = count if count is not None else (existing.count or 0) + 1
            existing.text = _excerpt(text) or existing.text
            existing.created_at = datetime.utcnow()
        else:
            db.add(
                Notification(
                    user_id=recipient_id,
                    actor_id=actor_id,
                    post_id=post_id,
                    kind=kind,
                    comment_id=comment_id,
                    text=_excerpt(text),
                    count=count if count is not None else 1,
                )
            )
        db.commit()
    except Exception:  # noqa: BLE001 - see module docstring
        db.rollback()
        logger.exception("could not record %s notification for %s", kind, recipient_id)
