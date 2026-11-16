from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.core.clock import utcnow
from app.database import Base


class Reviewer(Base):
    """An address the owner added to the review team from the app (the other way in is ADMIN_EMAILS).

    Kept by address, not by account, because the person may not have signed in yet: whoever signs in with
    Google under this address becomes a reviewer, and stops being one the moment the row is removed.
    The address is stored lower-case. Owners are never made here: that stays in OWNER_EMAILS.
    """

    __tablename__ = "reviewers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
