from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.notification import FOLLOW, Notification
from app.models.user import User
from app.schemas.notification import MarkRead, NotificationResponse, UnreadCount

router = APIRouter()


def _unread(db: Session, user: User) -> int:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.read_at.is_(None))
        .count()
    )


@router.get("", response_model=List[NotificationResponse])
def list_notifications(
    skip: int = Query(0, ge=0, le=10000),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What readers did on the signed-in author's stories, newest first."""
    rows = (
        db.query(Notification)
        .options(selectinload(Notification.actor), selectinload(Notification.post))
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [
        NotificationResponse(
            id=row.id,
            kind=row.kind,
            count=row.count or 1,
            text=row.text,
            comment_id=row.comment_id,
            created_at=row.created_at,
            read=row.read_at is not None,
            actor=row.actor,
            post=row.post,
        )
        for row in rows
        # A story event whose story is gone is not worth showing.
        if row.actor is not None and (row.post is not None or row.kind == FOLLOW)
    ]


@router.get("/unread-count", response_model=UnreadCount)
def unread_count(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return UnreadCount(count=_unread(db, current_user))


@router.post("/read", response_model=UnreadCount)
def mark_read(
    payload: MarkRead,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark the given notifications (or all of them) read; returns what is still unread."""
    query = db.query(Notification).filter(
        Notification.user_id == current_user.id, Notification.read_at.is_(None)
    )
    if payload.ids is not None:
        query = query.filter(Notification.id.in_(payload.ids[:500]))
    query.update({Notification.read_at: datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return UnreadCount(count=_unread(db, current_user))
