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
    content_html = Column(Text, nullable=True)  # sanitized rich-text rendering
    slug = Column(String, unique=True, index=True, nullable=False)
    cover_image = Column(String, nullable=True)
    topic = Column(String, nullable=True, index=True)
    clap_count = Column(Integer, default=0, nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    published = Column(Integer, default=0)  # 0 = draft, 1 = published
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    author = relationship("User", back_populates="posts")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    claps = relationship("Clap", back_populates="post", cascade="all, delete-orphan")
    bookmarks = relationship("Bookmark", back_populates="post", cascade="all, delete-orphan")
    tags = relationship("Tag", secondary="post_tags", back_populates="posts", lazy="selectin")

    @property
    def tag_names(self):
        return [tag.name for tag in (self.tags or [])]

