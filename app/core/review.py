"""Shared steps of the review flow, used by the review endpoints and by the story editor."""

import logging
from typing import List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.admin import admin_emails
from app.core.notify import notify
from app.models.notification import REVIEW_RESUBMIT
from app.models.post import Post
from app.models.user import User

logger = logging.getLogger(__name__)


def reviewers(db: Session) -> List[User]:
    """The accounts allowed to review: listed in ADMIN_EMAILS and verified by Google."""
    emails = admin_emails()
    if not emails:
        return []
    return db.query(User).filter(func.lower(User.email).in_(emails), User.email_verified.is_(True)).all()


def tell_reviewers_of_resubmission(db: Session, post: Post, writer: User) -> None:
    """One notification per reviewer: the writer has sent the corrected story back."""
    for reviewer in reviewers(db):
        notify(
            db,
            actor_id=writer.id,
            kind=REVIEW_RESUBMIT,
            post=post,
            recipient_id=reviewer.id,
            text=post.title,
        )
