"""Membership: what a reader can buy, and whether they were a member at a given moment.

A membership is made of *periods*, one for every payment that was captured: a month or a year of
paid time that starts when the previous one ends (or now, if there is none left). The question
"was this reader a member when they clapped?" is answered from the periods alone, so a past month
can always be worked out again the way it was, even after the membership lapsed.

Membership here is patron-style: it supports the site and gives a badge, and the reader's
reactions weigh more in writers' monthly points. It locks no story behind a wall.
"""

import calendar
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.models.payments import ACTIVE, CANCELLED, Membership, MembershipPeriod, Payment


@dataclass(frozen=True)
class Plan:
    code: str
    months: int
    price_paise: int


def plans() -> Dict[str, Plan]:
    """The plans on sale, in the order they are shown. Prices come from the settings, never from a request."""
    return {
        "monthly": Plan("monthly", 1, settings.MEMBERSHIP_MONTHLY_RUPEES * 100),
        "yearly": Plan("yearly", 12, settings.MEMBERSHIP_YEARLY_RUPEES * 100),
    }


def add_months(moment: datetime, months: int) -> datetime:
    """``moment`` plus whole calendar months (the day is cut to the end of a shorter month: 31 Jan + 1 = 28 Feb)."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def paid_until(db: Session, user_id: int) -> Optional[datetime]:
    """When the reader's last paid period ends (None if they never paid, or every payment was refunded)."""
    return (
        db.query(func.max(MembershipPeriod.ends_at))
        .filter(MembershipPeriod.user_id == user_id, MembershipPeriod.voided_at.is_(None))
        .scalar()
    )


def is_member(db: Session, user_id: int, at: Optional[datetime] = None) -> bool:
    at = at or utcnow()
    return (
        db.query(MembershipPeriod.id)
        .filter(
            MembershipPeriod.user_id == user_id,
            MembershipPeriod.voided_at.is_(None),
            MembershipPeriod.starts_at <= at,
            MembershipPeriod.ends_at > at,
        )
        .first()
        is not None
    )


def member_ids(db: Session, user_ids: List[int], at: Optional[datetime] = None) -> set:
    """Which of ``user_ids`` hold a membership right now (one query, for lists of people)."""
    if not user_ids:
        return set()
    at = at or utcnow()
    rows = (
        db.query(MembershipPeriod.user_id)
        .filter(
            MembershipPeriod.user_id.in_(user_ids),
            MembershipPeriod.voided_at.is_(None),
            MembershipPeriod.starts_at <= at,
            MembershipPeriod.ends_at > at,
        )
        .distinct()
    )
    return {row[0] for row in rows}


def membership_lookup(db: Session, start: datetime, end: datetime) -> Callable[[int, datetime], bool]:
    """``was_member(user_id, moment)`` for moments in [start, end), from one query up front."""
    spans: Dict[int, List[Tuple[datetime, datetime]]] = {}
    rows = db.query(MembershipPeriod.user_id, MembershipPeriod.starts_at, MembershipPeriod.ends_at).filter(
        MembershipPeriod.voided_at.is_(None), MembershipPeriod.starts_at < end, MembershipPeriod.ends_at > start
    )
    for user_id, starts_at, ends_at in rows:
        spans.setdefault(user_id, []).append((starts_at, ends_at))

    def was_member(user_id: int, moment: datetime) -> bool:
        return any(s <= moment < e for s, e in spans.get(user_id, ()))

    return was_member


def grant_period(db: Session, payment: Payment, months: int, now: Optional[datetime] = None) -> MembershipPeriod:
    """Add the paid time for a captured membership payment, after whatever time the reader still has.
    Does not commit. Doing it twice for the same payment returns the first period."""
    existing = db.query(MembershipPeriod).filter(MembershipPeriod.payment_id == payment.id).first()
    if existing is not None:
        return existing
    now = now or utcnow()
    left = paid_until(db, payment.user_id)
    starts = left if left and left > now else now
    period = MembershipPeriod(user_id=payment.user_id, payment_id=payment.id, starts_at=starts, ends_at=add_months(starts, months))
    db.add(period)

    membership = db.query(Membership).filter(Membership.user_id == payment.user_id).first()
    if membership is None:
        membership = Membership(user_id=payment.user_id, plan_code=payment.plan_code or "monthly", started_at=now)
        db.add(membership)
    membership.plan_code = payment.plan_code or membership.plan_code
    membership.status = ACTIVE
    membership.cancelled_at = None
    if payment.provider_subscription_id:
        membership.provider_subscription_id = payment.provider_subscription_id
        membership.auto_renew = True
    db.flush()
    return period


def void_period(db: Session, payment: Payment, now: Optional[datetime] = None) -> None:
    """A refund of the whole price takes the paid time back. Does not commit."""
    period = db.query(MembershipPeriod).filter(MembershipPeriod.payment_id == payment.id).first()
    if period is not None and period.voided_at is None:
        period.voided_at = now or utcnow()


def stop_renewing(db: Session, user_id: int, now: Optional[datetime] = None) -> Optional[Membership]:
    """The reader (or the provider) ended auto-renewal. They stay a member until the paid time runs out."""
    membership = db.query(Membership).filter(Membership.user_id == user_id).first()
    if membership is not None:
        membership.auto_renew = False
        membership.status = CANCELLED
        membership.cancelled_at = now or utcnow()
    return membership


def shows_badge(db: Session, user_id: int) -> bool:
    """Whether the reader's public profile carries the member badge: they hold a membership and have not hidden it."""
    membership = db.query(Membership).filter(Membership.user_id == user_id).first()
    return bool(membership and membership.show_badge and is_member(db, user_id))


def status_for(db: Session, user_id: int, now: Optional[datetime] = None) -> Dict[str, object]:
    """What the reader's membership page shows."""
    now = now or utcnow()
    membership = db.query(Membership).filter(Membership.user_id == user_id).first()
    end = paid_until(db, user_id)
    active = bool(end and end > now and is_member(db, user_id, now))
    return {
        "is_member": active,
        "ever_member": membership is not None and end is not None,
        "plan_code": membership.plan_code if membership else None,
        "paid_until": end if end and end > now else None,
        # Renews on its own only while it is active; a cancelled one simply runs out.
        "auto_renew": bool(membership and membership.auto_renew and active),
        "cancelled": bool(membership and membership.cancelled_at is not None and active),
        "show_badge": membership.show_badge if membership else True,
        "since": membership.started_at if membership else None,
    }
