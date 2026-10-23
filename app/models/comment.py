from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import backref, relationship
from datetime import datetime
from app.database import Base

class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    # The passage of the story this response was written about (selected while reading).
    quote = Column(Text, nullable=True)
    # Where that passage starts in the story's collapsed text (NULL for older responses).
    quote_start = Column(Integer, nullable=True)
    # The top-level response this one answers; NULL for a top-level response itself.
    parent_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    post = relationship("Post", back_populates="comments")
    author = relationship("User", back_populates="comments")
    # Answers to this response. The ORM deletes them with it (and orders the DELETEs),
    # which also covers SQLite, where the foreign key is not enforced.
    replies = relationship(
        "Comment",
        backref=backref("parent", remote_side=[id]),
        cascade="all, delete-orphan",
        order_by="Comment.created_at",
    )
