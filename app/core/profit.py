"""The owner's monthly sheet: what came in, what it cost, and the pool that follows from it.

Everything on the sheet is worked out from the books each time it is opened (``app.core.ledger``),
so it can never disagree with them. The money is counted when it is received, not spread over the
time it covers; a yearly membership therefore lands in one month, and the sheet lists those on a line of their own
so the owner can see the lump and hold some back.

What the sheet *decides* is kept apart from what it *shows*: the owner's chosen percent for the
pool and for the hold-back live in ``profit_sheets``, one row per month, and a month with no row
simply reuses the most recent earlier choice. The result is a suggestion. The pool itself is
still set by hand in the reviewers' desk, or by the owner pressing "use this" (which only fills in the
same field).

Rules of the suggestion, in the order they apply:
  * a month with no profit has no pool (a loss is not carried into later months);
  * the pool is the chosen percent of the profit, in whole rupees, rounded down;
  * a chosen part of it is held back for refunds that arrive late, and added to the next month's suggestion.

This is for the owner's eyes only; nothing here is reachable by a reviewer or a writer.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import ledger as books
from app.core.earnings import month_bounds, previous_month
from app.models.ledger import ProfitSheet
from app.models.partner import EarningMonth
from app.models.payments import PAID_STATUSES, TIP, Payment

RUPEE = 100


def whole_rupees(paise: int) -> int:
    return (paise // RUPEE) * RUPEE


@dataclass
class Choice:
    """The owner's percent for a month, and whether it was typed for that month or carried over."""

    pool_percent: Optional[int] = None
    holdback_percent: int = 0
    note: Optional[str] = None
    own: bool = False


def choice_for(db: Session, month: str) -> Choice:
    row = db.query(ProfitSheet).filter(ProfitSheet.month == month).first()
    if row is not None:
        return Choice(row.pool_percent, row.holdback_percent or 0, row.note, own=True)
    earlier = (
        db.query(ProfitSheet)
        .filter(ProfitSheet.month < month, ProfitSheet.pool_percent.isnot(None))
        .order_by(ProfitSheet.month.desc())
        .first()
    )
    if earlier is not None:
        return Choice(earlier.pool_percent, earlier.holdback_percent or 0, None, own=False)
    return Choice()


def split_pool(profit: int, pool_percent: Optional[int], holdback_percent: int) -> Tuple[int, int]:
    """(pool before the hold-back, the part held back). Both whole rupees; (0, 0) with no profit or no percent."""
    if pool_percent is None or profit <= 0:
        return 0, 0
    pool = whole_rupees(profit * pool_percent // 100)
    return pool, whole_rupees(pool * holdback_percent // 100)


@dataclass
class Sheet:
    month: str
    income: Dict[str, int] = field(default_factory=dict)
    costs: Dict[str, int] = field(default_factory=dict)
    income_total: int = 0
    cost_total: int = 0
    profit: int = 0
    # Money that passes through and is not the site's: tips, and the GST collected for the tax office.
    tips_gross: int = 0
    tips_to_writers: int = 0
    gst_collected: int = 0
    yearly_received: int = 0
    choice: Choice = field(default_factory=Choice)
    carried_in: int = 0
    pool_before_holdback: int = 0
    held_back: int = 0
    suggested: Optional[int] = None
    pool_set: Optional[int] = None
    finalized: bool = False


def _figures(db: Session, month: str) -> Tuple[Dict[str, int], Dict[str, int]]:
    start, end = month_bounds(month)
    totals = books.totals_by_account(db, start, end)
    return (
        {code: totals.get(code, 0) for code in books.INCOME},
        {code: totals.get(code, 0) for code in books.EXPENSE},
    )


def profit_of(db: Session, month: str) -> int:
    income, costs = _figures(db, month)
    return sum(income.values()) - sum(costs.values())


def held_back_in(db: Session, month: str) -> int:
    """What that month's own sheet keeps back (it is added to the following month's suggestion)."""
    choice = choice_for(db, month)
    return split_pool(profit_of(db, month), choice.pool_percent, choice.holdback_percent)[1]


def sheet_for(db: Session, month: str) -> Sheet:
    start, end = month_bounds(month)
    income, costs = _figures(db, month)
    sheet = Sheet(month=month, income=income, costs=costs)
    sheet.income_total, sheet.cost_total = sum(income.values()), sum(costs.values())
    sheet.profit = sheet.income_total - sheet.cost_total

    paid_tips = db.query(func.coalesce(func.sum(Payment.amount_paise), 0), func.coalesce(func.sum(Payment.writer_share_paise), 0)).filter(
        Payment.purpose == TIP, Payment.status.in_(PAID_STATUSES), Payment.captured_at >= start, Payment.captured_at < end
    ).one()
    sheet.tips_gross, sheet.tips_to_writers = int(paid_tips[0]), int(paid_tips[1])
    sheet.gst_collected = books.totals_by_account(db, start, end).get(books.GST, 0)
    sheet.yearly_received = int(
        db.query(func.coalesce(func.sum(Payment.amount_paise), 0))
        .filter(Payment.plan_code == "yearly", Payment.status.in_(PAID_STATUSES), Payment.captured_at >= start, Payment.captured_at < end)
        .scalar()
        or 0
    )

    sheet.choice = choice_for(db, month)
    sheet.pool_before_holdback, sheet.held_back = split_pool(sheet.profit, sheet.choice.pool_percent, sheet.choice.holdback_percent)
    sheet.carried_in = held_back_in(db, previous_month(month))
    if sheet.choice.pool_percent is not None:
        sheet.suggested = sheet.pool_before_holdback - sheet.held_back + sheet.carried_in

    row = db.query(EarningMonth).filter(EarningMonth.month == month).first()
    if row is not None:
        sheet.pool_set, sheet.finalized = row.pool_paise, row.finalized_at is not None
    return sheet
