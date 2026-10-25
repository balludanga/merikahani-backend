from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text

from app.database import Base


class StoryAudio(Base):
    """The narrated MP3 of one story, made the first time someone listens.

    One row per story. ``content_hash`` covers the title, the text, the voice and the pacing
    rules, so editing the story (or changing the voice) makes the next listen produce fresh
    audio, while every listen after that is a plain database read and costs no quota.
    """

    __tablename__ = "story_audio"

    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    content_hash = Column(String(64), nullable=False)
    voice = Column(String(60), nullable=False)
    audio = Column(LargeBinary, nullable=False)
    chars = Column(Integer, nullable=False, default=0)
    # When each word is spoken (JSON from app.core.timing), for the follow-along highlight.
    # Narrations made before this existed have none and get an estimate.
    timings = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TtsUsage(Base):
    """Characters sent to the speech service per calendar month ("2026-10"), for the quota guard."""

    __tablename__ = "tts_usage"

    month = Column(String(7), primary_key=True)
    chars = Column(Integer, nullable=False, default=0)
