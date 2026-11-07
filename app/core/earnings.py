"""The monthly pool: whose readers did what, and how a month's money is shared out.

A partner earns *points* from what other, established readers do on their public stories:

  * a reader's first clap on a story ................ CLAP_POINTS, once per reader per story
  * a highlight ..................................... HIGHLIGHT_POINTS, at most MAX_HIGHLIGHTS per reader per story per month
  * a response or a reply ........................... RESPONSE_POINTS, at most MAX_RESPONSES per reader per story per month

Those are base points. Each is then multiplied by a whole-number weight that depends on whether
the reader held a membership at the moment they acted (see ``weights_for``): with the site-wide
default of 1 and 1 nothing changes, with 1 and 0 only members' reactions count. A reaction whose
weight is zero is not counted at all, so it shows up neither in the totals nor in the reader count.

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
from app.core.membership import membership_lookup
from app.core.payments import accounting
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.highlight import Highlight
from app.models.partner import APPROVED, EarningLine, EarningMonth, PartnerApplication, Payout
from app.models.payments import PAID_STATUSES, TIP, Payment
from app.models.post import Post
from app.models.user import User

IST = timedelta(hours=5, minutes=30)

CLAP_POINTS = 1
HIGHLIGHT_POINTS = 2
MAX_HIGHLIGHTS = 3
RESPONSE_POINTS = 5
MAX_RESPONSES = 2
BASE_POINTS = {"claps": CLAP_POINTS, "highlights": HIGHLIGHT_POINTS, "responses": RESPONSE_POINTS}
# The most a single reaction may be worth, as a multiple of its base points.
MAX_WEIGHT = 10

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

def weights_for(month: Optional[EarningMonth] = None) -> Tuple[int, int]:
    """(member weight, non-member weight) for a month: its own, or else the site-wide setting.

    A member's reaction is always worth at least its base points; a non-member's may be worth none."""
    member = month.member_weight if month is not None and month.member_weight is not None else settings.POINTS_MEMBER_WEIGHT
    other = month.nonmember_weight if month is not None and month.nonmember_weight is not None else settings.POINTS_NONMEMBER_WEIGHT
    return max(1, min(MAX_WEIGHT, int(member))), max(0, min(MAX_WEIGHT, int(other)))


@dataclass
class Tally:
    """What one partner's stories earned in a month."""

    user_id: int
    claps: int = 0
    highlights: int = 0
    responses: int = 0
    # Weighted points, and the part of them that came from readers who held a membership.
    points: int = 0
    member_points: int = 0
    # reader id -> points that reader gave this writer: how many people, and how lopsided.
    by_reader: Dict[int, int] = field(default_factory=dict)

    @property
    def readers(self) -> int:
        return len(self.by_reader)

    @property
    def top_reader_pct(self) -> int:
        """The biggest single reader's share of the points, in percent (0 when there are none)."""
        total = sum(self.by_reader.values())
        return round(100 * max(self.by_reader.values()) / total) if total else 0

    def add(self, reader_id: int, kind: str, n: int = 1, weight: int = 1, member: bool = False) -> None:
        if n <= 0 or weight <= 0:
            return
        gained = n * BASE_POINTS[kind] * weight
        setattr(self, kind, getattr(self, kind) + n)
        self.points += gained
        if member:
            self.member_points += gained
        self.by_reader[reader_id] = self.by_reader.get(reader_id, 0) + gained


def engagement(
    db: Session, key: str, member_weight: Optional[int] = None, nonmember_weight: Optional[int] = None
) -> Dict[int, Tally]:
    """Every partner's tally for the month (partners with nothing get an empty one).

    The weights default to the site-wide setting; a month that has its own passes them in."""
    if member_weight is None or nonmember_weight is None:
        default_member, default_other = weights_for(None)
        member_weight = default_member if member_weight is None else member_weight
        nonmember_weight = default_other if nonmember_weight is None else nonmember_weight
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
    was_member = membership_lookup(db, start, end)

    def counts(author_id: int, reader_id: int, reader_since: Optional[datetime], at: datetime) -> bool:
        return (
            reader_id != author_id
            and reader_since is not None
            and reader_since <= at - grown_up
            and at >= members[author_id]
        )

    def weigh(reader_id: int, at: datetime) -> Tuple[int, bool]:
        """(what this reaction is worth, whether the reader was a member when they made it)."""
        member = was_member(reader_id, at)
        return (member_weight if member else nonmember_weight), member

    for author_id, reader_id, at, since in (
        db.query(Post.author_id, Clap.user_id, Clap.created_at, User.created_at)
        .select_from(Clap)
        .join(Post, Post.id == Clap.post_id)
        .join(User, User.id == Clap.user_id)
        .filter(Post.published == 1, Post.author_id.in_(authors), Clap.created_at >= start, Clap.created_at < end)
    ):
        if counts(author_id, reader_id, since, at):
            weight, member = weigh(reader_id, at)
            tallies[author_id].add(reader_id, "claps", 1, weight, member)

    # Highlights and responses are capped per reader per story, so collect them in groups first. The
    # cap keeps a reader's EARLIEST reactions, and each one is weighed by when it was made.
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
        groups: Dict[Tuple[int, int, int], List[datetime]] = {}
        for author_id, reader_id, post_id, at, since in rows:
            if counts(author_id, reader_id, since, at):
                groups.setdefault((author_id, reader_id, post_id), []).append(at)
        for (author_id, reader_id, _post_id), times in groups.items():
            for at in sorted(times)[:cap]:
                weight, member = weigh(reader_id, at)
                tallies[author_id].add(reader_id, kind, 1, weight, member)

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
    weights: Optional[Tuple[int, int]] = None,
) -> List[MonthRow]:
    """Everyone's tally and share for a month, biggest first. Used for the preview and, unchanged,
    for finalizing, so what the owner approves is exactly what gets stored."""
    excluded = excluded or {}
    tallies = engagement(db, key, *(weights or (None, None)))
    amounts = share_out({user_id: t.points for user_id, t in tallies.items()}, pool_paise, max_per_point_paise, excluded)
    rows = [MonthRow(t, amounts.get(user_id, 0), excluded.get(user_id)) for user_id, t in tallies.items()]
    rows.sort(key=lambda row: (-row.amount_paise, -row.tally.points, row.tally.user_id))
    return rows


def finalize_month(db: Session, month: EarningMonth, admin_id: int, excluded: Dict[int, str]) -> None:
    """Freeze a finished month: one line per partner, and the totals. The caller commits."""
    # Freeze the weights with the month, so reading it back later shows the rule it was worked out by.
    month.member_weight, month.nonmember_weight = weights_for(month)
    rows = compute_month(
        db, month.month, month.pool_paise, month.max_per_point_paise, excluded, (month.member_weight, month.nonmember_weight)
    )
    lines = []
    for row in rows:
        t = row.tally
        lines.append(
            EarningLine(
                month_id=month.id,
                user_id=t.user_id,
                claps=t.claps,
                highlights=t.highlights,
                responses=t.responses,
                readers=t.readers,
                points=t.points,
                member_points=t.member_points,
                amount_paise=row.amount_paise,
                held_reason=row.held_reason,
            )
        )
    db.add_all(lines)
    month.total_points = sum(row.tally.points for row in rows if row.held_reason is None)
    month.distributed_paise = sum(row.amount_paise for row in rows)
    month.finalized_at = utcnow()
    month.finalized_by = admin_id
    db.flush()
    # What the pool handed out is now owed to those writers: into the books, in the same transaction.
    accounting.record_pool_month(db, month, lines, admin_id)


# --------------------------------------------------------------------------- what a writer is owed

def tips_paise(db: Session, user_id: int) -> int:
    """What readers' tips have added to the writer's balance: their share of every tip that was paid, less
    the part of it that was refunded."""
    credited = (
        db.query(func.coalesce(func.sum(Payment.writer_share_paise - Payment.refunded_primary_paise), 0))
        .filter(Payment.target_user_id == user_id, Payment.purpose == TIP, Payment.status.in_(PAID_STATUSES))
        .scalar()
    )
    return int(credited or 0)


def totals_split(db: Session, user_id: int) -> Tuple[int, int, int]:
    """(from the pool, from tips, paid out) in paise, over finalized months, paid tips and recorded payouts."""
    pool = db.query(func.coalesce(func.sum(EarningLine.amount_paise), 0)).filter(EarningLine.user_id == user_id).scalar()
    paid = db.query(func.coalesce(func.sum(Payout.amount_paise), 0)).filter(Payout.user_id == user_id).scalar()
    return int(pool or 0), tips_paise(db, user_id), int(paid or 0)


def totals(db: Session, user_id: int) -> Tuple[int, int]:
    """(earned, paid) in paise: pool earnings and tips together, and what has been paid out."""
    pool, tips, paid = totals_split(db, user_id)
    return pool + tips, paid


def balance_paise(db: Session, user_id: int) -> int:
    earned, paid = totals(db, user_id)
    return earned - paid


def to_paise(rupees) -> int:
    """Whole paise from a validated rupee amount (at most two decimals)."""
    return int(round(rupees * 100))
