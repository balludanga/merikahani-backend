"""Remember requests that ended in a server error, so the owner's status page can show them.

Recording is best effort: it must never turn one failure into two, so it swallows its own errors.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from app.database import SessionLocal
from app.models.server_error import ServerError

logger = logging.getLogger(__name__)

KEEP_DAYS = 30
KEEP_ROWS = 500


def describe(exc: BaseException) -> str:
    """Class name and first line of the message, without SQL or its parameters (those can hold
    emails and other personal data)."""
    message = str(exc).split("[SQL:")[0].split("\n")[0].strip()
    return f"{type(exc).__name__}: {message}"[:300] if message else type(exc).__name__


def record_server_error(method: str, path: str, status: int, error: Optional[str]) -> None:
    db = SessionLocal()
    try:
        db.add(ServerError(method=method[:10], path=path[:300], status=status, error=(error or None)))
        db.commit()
        # Housekeeping on the (rare) write: nothing older than a month, nothing past 500 rows.
        db.query(ServerError).filter(ServerError.created_at < datetime.utcnow() - timedelta(days=KEEP_DAYS)).delete()
        cutoff = db.query(ServerError.id).order_by(ServerError.id.desc()).offset(KEEP_ROWS).first()
        if cutoff is not None:
            db.query(ServerError).filter(ServerError.id <= cutoff[0]).delete()
        db.commit()
    except Exception:  # noqa: BLE001 - see module docstring
        db.rollback()
        logger.exception("could not record a server error")
    finally:
        db.close()
