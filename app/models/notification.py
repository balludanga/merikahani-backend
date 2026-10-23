from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base

# What happened to the author's story.
COMMENT = "comment"
CLAP = "clap"
HIGHLIGHT = "highlight"
FOLLOW = "follow"


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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    read_at = Column(DateTime, nullable=True)

    actor = relationship("User", foreign_keys=[actor_id])
    post = relationship("Post", back_populates="notifications")
