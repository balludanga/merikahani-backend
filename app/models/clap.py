from sqlalchemy import Column, Integer, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Clap(Base):
    __tablename__ = "claps"
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_clap_user_post"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    count = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")
    post = relationship("Post", back_populates="claps")
