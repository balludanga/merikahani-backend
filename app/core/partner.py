"""Who may join the write-and-earn programme, and where each writer stands with it.

The rules live here so the application endpoint, the writer's own page and the reviewers' desk
all read the same numbers. The follower line is a hard gate checked by the server on every
application (the page only shows progress); what the review team adds is judgement, and
``applicant_signals`` gives them what a follower count alone cannot: how real those followers look.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.core.notify import notify
from app.core.review import reviewers
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.follow import Follow
from app.models.highlight import Highlight
from app.models.notification import PARTNER_APPLIED, PARTNER_ELIGIBLE
from app.models.partner import APPROVED, DECLINED, PENDING, REVOKED, PartnerApplication
from app.models.post import Post
from app.models.user import User

logger = logging.getLogger(__name__)

NONE = "none"  # the status of a writer who never applied


@dataclass
class Requirement:
    have: int
    need: int

    @property
    def met(self) -> bool:
        return self.have >= self.need


def follower_count(db: Session, user_id: int) -> int:
    return db.query(func.count(Follow.id)).filter(Follow.following_id == user_id).scalar() or 0


def story_count(db: Session, user_id: int) -> int:
    return db.query(func.count(Post.id)).filter(Post.author_id == user_id, Post.published == 1).scalar() or 0


def get_application(db: Session, user_id: int) -> Optional[PartnerApplication]:
    return db.query(PartnerApplication).filter(PartnerApplication.user_id == user_id).first()


def status_of(application: Optional[PartnerApplication]) -> str:
    return application.status if application else NONE


def requirements(db: Session, user_id: int) -> Tuple[Requirement, Requirement]:
    """(followers, stories): what the writer has against what the programme asks."""
    return (
        Requirement(follower_count(db, user_id), settings.PARTNER_MIN_FOLLOWERS),
        Requirement(story_count(db, user_id), settings.PARTNER_MIN_STORIES),
    )


def reapply_on(application: Optional[PartnerApplication]) -> Optional[datetime]:
    """When a declined writer may apply again; None if they were not declined."""
    if application and application.status == DECLINED and application.decided_at:
        return application.decided_at + timedelta(days=settings.PARTNER_REAPPLY_DAYS)
    return None


def blocker(
    db: Session, user_id: int, application: Optional[PartnerApplication], now: Optional[datetime] = None
) -> Optional[str]:
    """Why this writer cannot apply right now, as the Hindi sentence they are shown, or None."""
    now = now or utcnow()
    status = status_of(application)
    if status == PENDING:
        return "आपका आवेदन पहले से समीक्षा में है"
    if status == APPROVED:
        return "आप पहले से साझेदार लेखक हैं"
    if status == REVOKED:
        return "आपकी साझेदारी रोकी गई है, इसलिए नया आवेदन नहीं हो सकता"
    again = reapply_on(application)
    if again is not None and now < again:
        days = max(1, math.ceil((again - now).total_seconds() / 86400))
        return f"पिछला आवेदन स्वीकृत नहीं हुआ था — आप {days} दिन बाद फिर आवेदन कर सकते हैं"
    followers, stories = requirements(db, user_id)
    if not followers.met:
        return f"आवेदन के लिए कम से कम {followers.need} फ़ॉलोअर्स चाहिए — अभी आपके {followers.have} हैं"
    if not stories.met:
        return f"आवेदन से पहले कम से कम {stories.need} रचना प्रकाशित करें"
    return None


def submit_application(db: Session, user: User, note: Optional[str]) -> PartnerApplication:
    """Record (or renew) the writer's application. The caller has already checked ``blocker``."""
    followers, stories = requirements(db, user.id)
    application = get_application(db, user.id)
    if application is None:
        application = PartnerApplication(user_id=user.id, attempts=1)
        db.add(application)
    else:
        application.attempts = (application.attempts or 1) + 1
    application.status = PENDING
    application.note = note
    application.followers_at_apply = followers.have
    application.stories_at_apply = stories.have
    application.applied_at = utcnow()
    application.decided_at = None
    application.decided_by = None
    application.decision_note = None
    db.commit()
    db.refresh(application)
    return application


def tell_reviewers_of_application(db: Session, writer: User, note: Optional[str]) -> None:
    """One notification per reviewer: this writer has applied."""
    for reviewer in reviewers(db):
        notify(db, actor_id=writer.id, kind=PARTNER_APPLIED, recipient_id=reviewer.id, text=note)


def tell_writer_they_can_apply(db: Session, user_id: int, followers: int, actor_id: int) -> None:
    """Once, when a writer's followers first reach the line, say that they can now apply.

    ``actor_id`` is whoever's follow tipped it over (a notification needs an actor row); the bell
    shows the site speaking, not that follower. Never raises: it rides on someone else's follow.
    """
    try:
        if followers < settings.PARTNER_MIN_FOLLOWERS or get_application(db, user_id) is not None:
            return
        notify(db, actor_id=actor_id, kind=PARTNER_ELIGIBLE, recipient_id=user_id)
    except Exception:  # noqa: BLE001 - a follow must never fail because of this
        db.rollback()
        logger.exception("could not tell %s they can apply to the partner programme", user_id)


def applicant_signals(db: Session, user: User, now: Optional[datetime] = None) -> Dict[str, object]:
    """What the review team looks at besides the follower count: how real the followers look, and
    how the writer's own stories are doing. Everything is a plain count over existing rows."""
    now = now or utcnow()
    follower_ids = select(Follow.follower_id).where(Follow.following_id == user.id)
    followers = follower_count(db, user.id)

    active = set()
    for column in (Post.author_id, Clap.user_id, Comment.author_id, Highlight.user_id):
        active.update(row[0] for row in db.query(column).filter(column.in_(follower_ids)).distinct())

    def gained(days: int) -> int:
        return (
            db.query(func.count(Follow.id))
            .filter(Follow.following_id == user.id, Follow.created_at >= now - timedelta(days=days))
            .scalar()
            or 0
        )

    published = db.query(Post).filter(Post.author_id == user.id, Post.published == 1)
    own_story_ids = select(Post.id).where(Post.author_id == user.id, Post.published == 1)
    return {
        "followers": followers,
        # Followers whose address Google vouched for.
        "followers_verified": db.query(func.count(User.id))
        .filter(User.id.in_(follower_ids), User.email_verified.is_(True))
        .scalar()
        or 0,
        # Followers whose account is under a week old.
        "followers_new": db.query(func.count(User.id))
        .filter(User.id.in_(follower_ids), User.created_at >= now - timedelta(days=7))
        .scalar()
        or 0,
        # Followers who have never written, clapped, responded or highlighted anything.
        "followers_inactive": max(0, followers - len(active)),
        # Followers the writer follows back: a ring of mutual follows is a common way to inflate.
        "followers_mutual": db.query(func.count(Follow.id))
        .filter(Follow.follower_id == user.id, Follow.following_id.in_(follower_ids))
        .scalar()
        or 0,
        "followers_week": gained(7),
        "followers_month": gained(30),
        "stories": published.count(),
        "latest_story_at": published.with_entities(func.max(Post.created_at)).scalar(),
        "claps_total": int(
            db.query(func.coalesce(func.sum(Post.clap_count), 0))
            .filter(Post.author_id == user.id, Post.published == 1)
            .scalar()
            or 0
        ),
        "responses_received": db.query(func.count(Comment.id))
        .filter(Comment.post_id.in_(own_story_ids), Comment.author_id != user.id)
        .scalar()
        or 0,
        "account_days": max(0, (now - user.created_at).days) if user.created_at else 0,
    }


def top_stories(db: Session, user_id: int, limit: int = 5) -> List[Post]:
    """The writer's most-clapped public stories: what a reviewer should read first."""
    return (
        db.query(Post)
        .filter(Post.author_id == user_id, Post.published == 1)
        .order_by(Post.clap_count.desc(), Post.id.desc())
        .limit(limit)
        .all()
    )
