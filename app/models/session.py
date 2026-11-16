from sqlalchemy import Column, DateTime, String

from app.core.clock import utcnow
from app.database import Base


class RevokedSession(Base):
    """A sign-in that was ended by pressing "sign out", so that one device stops being signed in.

    Every sign-in token carries a random session id (``sid``) that stays the same as the token is
    renewed. Signing out writes that id here; a token whose id is listed is refused. Nothing else about
    the person is stored. A row is only needed while the sign-in could still be renewed, so
    ``expires_at`` is the outer limit of that chain and older rows are swept away at the next sign-out.
    """

    __tablename__ = "revoked_sessions"

    sid = Column(String(32), primary_key=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow)
