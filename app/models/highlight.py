from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text, func

from app.database import Base


class Highlight(Base):
    """A passage of a post the reader marked while reading."""

    __tablename__ = "highlights"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
