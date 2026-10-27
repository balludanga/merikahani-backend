"""One clock for the whole app: what "now" means when the columns are naive UTC.

Every ``DateTime`` column here is stored without a time zone — SQLite has no zone at all, and the
production columns were created as ``timestamp without time zone``. So a value that carries a
``tzinfo`` cannot be compared against what is in the row (``now - stored`` raises
``TypeError: can't subtract offset-naive and offset-aware datetimes``), which is exactly what the
audio quota, the ops counters, notification pruning and token expiry all do.

``datetime.utcnow()`` returns the right shape but is deprecated from Python 3.12, and the
suggested replacement (``datetime.now(timezone.utc)``) returns the wrong one. Keeping the
conversion in this one function means the rest of the code never has to remember which shape the
database is in — and never quietly starts comparing a zone-aware value against a naive column.
"""

from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Now, in UTC, with no tzinfo — the shape the stored timestamps are in."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def from_timestamp(seconds: float) -> datetime:
    """The same clock, read from a Unix timestamp (``time.time()``, ``STARTED_AT``).

    Replaces the deprecated ``datetime.utcfromtimestamp``, which assumed UTC while
    ``datetime.fromtimestamp`` would follow the machine's zone.
    """
    return datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=None)


def epoch_seconds(dt: Optional[datetime] = None) -> float:
    """Unix seconds for a stored (naive UTC) timestamp, or for this moment.

    ``naive.timestamp()`` reads the value as *local* time, so the number it gives differs by
    hours on a machine that is not in UTC. Cached URLs and ETags are built from it, so they have
    to be produced the same way wherever the app happens to run.
    """
    moment = utcnow() if dt is None else dt
    return moment.replace(tzinfo=timezone.utc).timestamp()
