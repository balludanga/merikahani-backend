from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.clock import utcnow
from app.database import Base

# Where a post stands with the reviewers (posts.moderation_state).
CLEAR = "none"                      # nothing pending: public if published, a plain draft otherwise
CHANGES_REQUESTED = "changes_requested"  # taken down; the writer is working through the notes
RESUBMITTED = "resubmitted"         # the writer sent it back; waiting for a reviewer

# Why a passage is flagged. The labels live in the frontend.
CATEGORIES = (
    "language",   # अशोभनीय भाषा
    "violence",   # हिंसा
    "hate",       # नफ़रत / भेदभाव
    "adult",      # वयस्क सामग्री
    "private",    # निजी जानकारी
    "copyright",  # कॉपीराइट / नकल
    "fact",       # तथ्य
    "style",      # भाषा-शैली / वर्तनी
    "other",
)

OPEN = "open"
FIXED = "fixed"


class ReviewRound(Base):
    """One time a reviewer sent a story back to its writer, with a short summary."""

    __tablename__ = "review_rounds"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    # Set when the reviewer approved the corrected story; empty while it is still open.
    approved_at = Column(DateTime, nullable=True)

    reviewer = relationship("User", foreign_keys=[reviewer_id])
    notes = relationship("ReviewNote", back_populates="round")


class ReviewNote(Base):
    """A reviewer's comment on one passage, optionally with the wording they suggest.

    ``text`` / ``start_offset`` / ``prefix`` / ``suffix`` anchor it the way a highlight is
    anchored (see Highlight). A note with no ``round_id`` is still a draft: only reviewers
    see it, and it is sent to the writer together with the others in one go.
    """

    __tablename__ = "review_notes"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    round_id = Column(Integer, ForeignKey("review_rounds.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    start_offset = Column(Integer, nullable=True)
    prefix = Column(String(80), nullable=True)
    suffix = Column(String(80), nullable=True)
    category = Column(String(16), nullable=False, default="other")
    comment = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    status = Column(String(8), nullable=False, default=OPEN)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    round = relationship("ReviewRound", back_populates="notes")
    reviewer = relationship("User", foreign_keys=[reviewer_id])
