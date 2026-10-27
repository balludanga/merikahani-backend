import json

from sqlalchemy import Boolean, Column, Integer, String, DateTime, Text, false
from sqlalchemy.orm import relationship
from app.core.clock import utcnow
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String, nullable=True)
    # Profile customisation (all optional; see app/schemas/profile.py for the rules).
    tagline = Column(String(120), nullable=True)      # one line under the name
    status_line = Column(String(120), nullable=True)  # "इन दिनों": what they are working on
    location = Column(String(80), nullable=True)
    accent = Column(String(7), nullable=True)         # "#rrggbb" the writer picked
    cover_style = Column(String(24), nullable=True)   # a banner design key
    cover_url = Column(String, nullable=True)         # their own uploaded banner
    genres_json = Column(Text, nullable=True)         # ["कहानी", ...]
    links_json = Column(Text, nullable=True)          # [{"label": ..., "url": ...}]
    pinned_json = Column(Text, nullable=True)         # [post ids]
    # 'password' | 'google'. Google accounts never log in with a password.
    auth_provider = Column(String, nullable=False, default="password", server_default="password")
    # True only when Google vouched for the address; password sign-ups are unverified.
    email_verified = Column(Boolean, nullable=False, default=False, server_default=false())
    # Bumped to invalidate every token issued earlier (takeover, password retired).
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    posts = relationship("Post", back_populates="author", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="author", cascade="all, delete-orphan")
    following = relationship(
        "Follow", foreign_keys="Follow.follower_id", back_populates="follower", cascade="all, delete-orphan"
    )
    followers = relationship(
        "Follow", foreign_keys="Follow.following_id", back_populates="following_user", cascade="all, delete-orphan"
    )

    @staticmethod
    def _load(raw):
        try:
            value = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return []
        return value if isinstance(value, list) else []

    @property
    def genres(self):
        return [g for g in self._load(self.genres_json) if isinstance(g, str)]

    @property
    def links(self):
        return [l for l in self._load(self.links_json) if isinstance(l, dict) and l.get("url")]

    @property
    def pinned_post_ids(self):
        return [i for i in self._load(self.pinned_json) if isinstance(i, int)]
