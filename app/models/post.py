from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    subtitle = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    cover_image = Column(String, nullable=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    published = Column(Integer, default=0)  # 0 = draft, 1 = published
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    author = relationship("User", back_populates="posts")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")

