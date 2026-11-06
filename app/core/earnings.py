"""The monthly pool: whose readers did what, and how a month's money is shared out.

A partner earns *points* from what other, established readers do on their public stories:

  * a reader's first clap on a story ................ CLAP_POINTS, once per reader per story
  * a highlight ..................................... HIGHLIGHT_POINTS, at most MAX_HIGHLIGHTS per reader per story per month
  * a response or a reply ........................... RESPONSE_POINTS, at most MAX_RESPONSES per reader per story per month

The pool for the month is split in proportion to points. Money is whole paise throughout and the
split uses the largest-remainder method, so the shares always add up to the pool to the paisa.

What never counts: the writer's own actions; a reader whose account was younger than
PARTNER_READER_MIN_AGE_DAYS when they acted (new accounts cost nothing to make); anything from
before the writer became a partner; anything on a story that is not public. A deleted response or
highlight is gone from its table, so it stops counting by itself.

Months are calendar months in Indian time, because that is how writers and the owner think of them.
Timestamps in the database are naive UTC, so the boundaries are shifted by IST.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.highlight import Highlight
from app.models.partner import APPROVED, EarningLine, EarningMonth, PartnerApplication, Payout
from app.models.post import Post
from app.models.user import User

IST = timedelta(hours=5, minutes=30)

CLAP_POINTS = 1
HIGHLIGHT_POINTS = 2
MAX_HIGHLIGHTS = 3
RESPONSE_POINTS = 5
MAX_RESPONSES = 2

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
EARLIEST_YEAR = 2024


# --------------------------------------------------------------------------- months

def month_key(moment: datetime) -> str:
    """The Indian calendar month a (naive UTC) moment falls in, as "2026-10"."""
    local = moment + IST
    return f"{local.year:04d}-{local.month:02d}"


def valid_month(key: str) -> bool:
    match = _MONTH.match(key or "")
    return bool(match) and int(match.group(1)) >= EARLIEST_YEAR


def month_bounds(key: str) -> Tuple[datetime, datetime]:
    """[start, end) of an Indian calendar month, as naive UTC. ``key`` must be ``valid_month``."""
    match = _MONTH.match(key)
    year, month = int(match.group(1)), int(match.group(2))
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start - IST, end - IST


def current_month(now: Optional[datetime] = None) -> str:
    return month_key(now or utcnow())


def previous_month(key: str) -> str:
    return month_key(month_bounds(key)[0] - timedelta(seconds=1))


def month_has_ended(key: str, now: Optional[datetime] = None) -> bool:
    return (now or utcnow()) >= month_bounds(key)[1]


# --------------------------------------------------------------------------- engagement

@dataclass
class Tally:
    """What one partner's stories earned in a month."""

    user_id: int
    claps: int = 0
    highlights: int = 0
    responses: int = 0
    # reader id -> points that reader gave this writer: how many people, and how lopsided.
    by_reader: Dict[int, int] = field(default_factory=dict)

    @property
    def points(self) -> int:
        return self.claps * CLAP_POINTS + self.highlights * HIGHLIGHT_POINTS + self.responses * RESPONSE_POINTS

    @property
    def readers(self) -> int:
        return len(self.by_reader)

    @property
    def top_reader_pct(self) -> int:
        """The biggest single reader's share of the points, in percent (0 when there are none)."""
        total = sum(self.by_reader.values())
        return round(100 * max(self.by_reader.values()) / total) if total else 0

    def add(self, reader_id: int, kind: str, n: int = 1) -> None:
        if n <= 0:
            return
        weight = {"claps": CLAP_POINTS, "highlights": HIGHLIGHT_POINTS, "responses": RESPONSE_POINTS}[kind]
        setattr(self, kind, getattr(self, kind) + n)
        self.by_reader[reader_id] = self.by_reader.get(reader_id, 0) + n * weight


def engagement(db: Session, key: str) -> Dict[int, Tally]:
    """Every partner's tally for the month (partners with nothing get an empty one)."""
    start, end = month_bounds(key)
    members: Dict[int, datetime] = {
        row.user_id: row.approved_at
        for row in db.query(PartnerApplication.user_id, PartnerApplication.approved_at).filter(
            PartnerApplication.status == APPROVED,
            PartnerApplication.approved_at.isnot(None),
            PartnerApplication.approved_at < end,
        )
    }
    tallies = {user_id: Tally(user_id) for user_id in members}
    if not members:
        return tallies
    authors = list(members)
    grown_up = timedelta(days=settings.PARTNER_READER_MIN_AGE_DAYS)

    def counts(author_id: int, reader_id: int, reader_since: Optional[datetime], at: datetime) -> bool:
        return (
            reader_id != author_id
            and reader_since is not None
            and reader_since <= at - grown_up
            and at >= members[author_id]
        )

    for author_id, reader_id, at, since in (
        db.query(Post.author_id, Clap.user_id, Clap.created_at, User.created_at)
        .select_from(Clap)
        .join(Post, Post.id == Clap.post_id)
        .join(User, User.id == Clap.user_id)
        .filter(Post.published == 1, Post.author_id.in_(authors), Clap.created_at >= start, Clap.created_at < end)
    ):
        if counts(author_id, reader_id, since, at):
            tallies[author_id].add(reader_id, "claps")

    # Highlights and responses are capped per reader per story, so count them in groups first.
    for kind, cap, rows in (
        (
            "highlights",
            MAX_HIGHLIGHTS,
            db.query(Post.author_id, Highlight.user_id, Highlight.post_id, Highlight.created_at, User.created_at)
            .select_from(Highlight)
            .join(Post, Post.id == Highlight.post_id)
            .join(User, User.id == Highlight.user_id)
            .filter(
                Post.published == 1,
                Post.author_id.in_(authors),
                Highlight.created_at >= start,
                Highlight.created_at < end,
            ),
        ),
        (
            "responses",
            MAX_RESPONSES,
            db.query(Post.author_id, Comment.author_id, Comment.post_id, Comment.created_at, User.created_at)
            .select_from(Comment)
            .join(Post, Post.id == Comment.post_id)
            .join(User, User.id == Comment.author_id)
            .filter(
                Post.published == 1,
                Post.author_id.in_(authors),
                Comment.created_at >= start,
                Comment.created_at < end,
            ),
        ),
    ):
        groups: Dict[Tuple[int, int, int], int] = {}
        for author_id, reader_id, post_id, at, since in rows:
            if counts(author_id, reader_id, since, at):
                groups[(author_id, reader_id, post_id)] = groups.get((author_id, reader_id, post_id), 0) + 1
        for (author_id, reader_id, _post_id), n in groups.items():
            tallies[author_id].add(reader_id, kind, min(n, cap))

    return tallies


# --------------------------------------------------------------------------- sharing the pool

def share_out(
    points: Dict[int, int],
    pool_paise: int,
    max_per_point_paise: Optional[int] = None,
    excluded: Iterable[int] = (),
) -> Dict[int, int]:
    """Split ``pool_paise`` over the writers in proportion to their points.

    Writers in ``excluded`` are left out before the split (their share goes to the others). The
    optional ceiling caps what one point can pay; the money it frees is not handed to anyone else.
    Every writer in ``points`` gets an entry, so the caller can show a zero.
    """
    left_out = set(excluded)
    counted = {user_id: p for user_id, p in points.items() if p > 0 and user_id not in left_out}
    total = sum(counted.values())
    amounts = {user_id: 0 for user_id in points}
    if total == 0 or pool_paise <= 0:
        return amounts

    remainders: List[Tuple[int, int]] = []
    for user_id, p in counted.items():
        amounts[user_id], remainder = divmod(pool_paise * p, total)
        remainders.append((remainder, user_id))
    # The paise left over go one each to the biggest remainders (ties: the lower id), which makes
    # the shares add up to the pool exactly and the result the same on every run.
    leftover = pool_paise - sum(amounts.values())
    for _remainder, user_id in sorted(remainders, key=lambda item: (-item[0], item[1]))[:leftover]:
        amounts[user_id] += 1

    if max_per_point_paise is not None:
        for user_id, p in counted.items():
            amounts[user_id] = min(amounts[user_id], p * max_per_point_paise)
    return amounts


@dataclass
class MonthRow:
    tally: Tally
    amount_paise: int
    held_reason: Optional[str] = None


def compute_month(
    db: Session,
    key: str,
    pool_paise: int,
    max_per_point_paise: Optional[int] = None,
    excluded: Optional[Dict[int, str]] = None,
) -> List[MonthRow]:
    """Everyone's tally and share for a month, biggest first. Used for the preview and, unchanged,
    for finalizing, so what the owner approves is exactly what gets stored."""
    excluded = excluded or {}
    tallies = engagement(db, key)
    amounts = share_out({user_id: t.points for user_id, t in tallies.items()}, pool_paise, max_per_point_paise, excluded)
    rows = [MonthRow(t, amounts.get(user_id, 0), excluded.get(user_id)) for user_id, t in tallies.items()]
    rows.sort(key=lambda row: (-row.amount_paise, -row.tally.points, row.tally.user_id))
    return rows


def finalize_month(db: Session, month: EarningMonth, admin_id: int, excluded: Dict[int, str]) -> None:
    """Freeze a finished month: one line per partner, and the totals. The caller commits."""
    rows = compute_month(db, month.month, month.pool_paise, month.max_per_point_paise, excluded)
    for row in rows:
        t = row.tally
        db.add(
            EarningLine(
                month_id=month.id,
                user_id=t.user_id,
                claps=t.claps,
                highlights=t.highlights,
                responses=t.responses,
                readers=t.readers,
                points=t.points,
                amount_paise=row.amount_paise,
                held_reason=row.held_reason,
            )
        )
    month.total_points = sum(row.tally.points for row in rows if row.held_reason is None)
    month.distributed_paise = sum(row.amount_paise for row in rows)
    month.finalized_at = utcnow()
    month.finalized_by = admin_id


# --------------------------------------------------------------------------- what a writer is owed

def totals(db: Session, user_id: int) -> Tuple[int, int]:
    """(earned, paid) in paise, over finalized months and recorded payouts."""
    earned = db.query(func.coalesce(func.sum(EarningLine.amount_paise), 0)).filter(EarningLine.user_id == user_id).scalar()
    paid = db.query(func.coalesce(func.sum(Payout.amount_paise), 0)).filter(Payout.user_id == user_id).scalar()
    return int(earned or 0), int(paid or 0)


def balance_paise(db: Session, user_id: int) -> int:
    earned, paid = totals(db, user_id)
    return earned - paid


def to_paise(rupees) -> int:
    """Whole paise from a validated rupee amount (at most two decimals)."""
    return int(round(rupees * 100))
