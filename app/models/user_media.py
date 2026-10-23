from sqlalchemy import Column, ForeignKey, Integer, LargeBinary, String

from app.database import Base


class UserMedia(Base):
    """A writer's own profile photo and banner, kept in the database.

    Stored here (not on the users row) so listing writers never drags image
    bytes along, and (not in object storage) so it works without any extra
    service. The browser crops and compresses first, so each is small.
    ``*_version`` is a Unix timestamp used as the cache-busting ``?v=``.
    """

    __tablename__ = "user_media"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    avatar = Column(LargeBinary, nullable=True)
    avatar_type = Column(String(20), nullable=True)
    avatar_version = Column(Integer, nullable=True)
    cover = Column(LargeBinary, nullable=True)
    cover_type = Column(String(20), nullable=True)
    cover_version = Column(Integer, nullable=True)
