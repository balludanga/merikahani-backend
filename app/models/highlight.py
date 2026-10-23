from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class Highlight(Base):
    """A passage of a post a reader marked while reading.

    ``text`` is the passage with whitespace collapsed. ``start_offset`` is where
    it begins in the story's collapsed text, and ``prefix`` / ``suffix`` are the
    words around it, so the passage can be found again after a reload even when
    the same words occur twice. Rows from before these existed have them NULL and
    fall back to a plain text search in the browser.
    """

    __tablename__ = "highlights"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    start_offset = Column(Integer, nullable=True)
    prefix = Column(String(80), nullable=True)
    suffix = Column(String(80), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User")
