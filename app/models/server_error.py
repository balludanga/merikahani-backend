from app.core.clock import utcnow

from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


class ServerError(Base):
    """A request that ended in a server error (HTTP 500 or worse), for the owner's status page.

    Only the route, the status and the exception's class and first line are kept: never the
    query string, the request body, headers, or any SQL parameters.
    """

    __tablename__ = "server_errors"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    method = Column(String(10), nullable=False)
    path = Column(String(300), nullable=False)
    status = Column(Integer, nullable=False)
    error = Column(String(300), nullable=True)
