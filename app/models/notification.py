from app.core.clock import utcnow

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base

# What happened to the author's story.
COMMENT = "comment"
CLAP = "clap"
HIGHLIGHT = "highlight"
FOLLOW = "follow"
# Someone answered one of your responses.
REPLY = "reply"
# A reviewer sent your story back with notes / approved it again; the writer sent it back to review.
REVIEW_CHANGES = "review_changes"
REVIEW_APPROVED = "review_approved"
REVIEW_RESUBMIT = "review_resubmit"
# Write and earn. A writer applied (told to the reviewers); the review team accepted, declined or
# removed them; they crossed the follower line; or they were paid (told to the writer).
PARTNER_APPLIED = "partner_applied"
PARTNER_APPROVED = "partner_approved"
PARTNER_DECLINED = "partner_declined"
PARTNER_REVOKED = "partner_revoked"
PARTNER_ELIGIBLE = "partner_eligible"
PAYOUT_PAID = "payout_paid"
# A reader sent the writer a tip. (`count` holds the writer's share in paise.)
TIP_RECEIVED = "tip"

# About a person or the programme rather than a story, so they carry no post.
POSTLESS = (
    FOLLOW, PARTNER_APPLIED, PARTNER_APPROVED, PARTNER_DECLINED, PARTNER_REVOKED, PARTNER_ELIGIBLE, PAYOUT_PAID,
    TIP_RECEIVED,
)
# Said by the review team, never by the account of the reviewer who acted.
REVIEW_TEAM_VOICE = (REVIEW_CHANGES, REVIEW_APPROVED, PARTNER_APPROVED, PARTNER_DECLINED, PARTNER_REVOKED)
# Said by the site itself. A tip is among them so an anonymous tipper's name never reaches the bell.
SITE_VOICE = (PARTNER_ELIGIBLE, PAYOUT_PAID, TIP_RECEIVED)


class Notification(Base):
    """Something a reader did on a story, shown to the story's author."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # who is told
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # who did it
    # Empty for a follow: it is about a person, not a story.
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=True, index=True)
    kind = Column(String(16), nullable=False)
    comment_id = Column(Integer, nullable=True)
    # A short excerpt: the response, or the highlighted passage (latest one when merged).
    text = Column(Text, nullable=True)
    # Claps given / passages highlighted by this reader (merged while unread).
    count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=utcnow, index=True)
    read_at = Column(DateTime, nullable=True)

    actor = relationship("User", foreign_keys=[actor_id])
    post = relationship("Post", back_populates="notifications")
