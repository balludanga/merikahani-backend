"""What each money event writes into the books. The only place that knows which accounts move.

  * A tip arrives:      cash up; the writer's share is owed to them; the site's share is income.
  * A membership:       cash up; the price without tax is income; the GST inside it is owed to the tax office.
  * A provider fee:     a cost, taken out of cash.
  * A refund:           the reverse of the above, in proportion to how much was refunded.
  * A month's pool:     the money handed to writers is owed to them (it is not a cost: the sheet leaves it out).
  * A payout:           what was owed to the writer is paid, cash down.

Nothing here commits; the caller does, in the same database transaction as the change that caused it.
"""

import uuid
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.core import ledger as books
from app.core.ledger import CASH, EXPENSE_FEES, GST, INCOME_MEMBERSHIP, INCOME_TIP_FEE, OWED, POOL, credit, debit
from app.core.payments.money import proportional
from app.models.ledger import LedgerTransaction
from app.models.partner import EarningLine, EarningMonth, Payout
from app.models.payments import MEMBERSHIP, PART_REFUNDED, REFUNDED, TIP, Payment


def record_capture(db: Session, payment: Payment) -> None:
    """The books for a payment that has just been captured (and its provider fee, if known)."""
    gross = int(payment.amount_paise)
    if payment.purpose == TIP:
        lines = [
            debit(CASH, gross),
            credit(OWED, payment.writer_share_paise or 0, payment.target_user_id),
            credit(INCOME_TIP_FEE, payment.site_fee_paise or 0),
        ]
    else:
        lines = [
            debit(CASH, gross),
            credit(INCOME_MEMBERSHIP, gross - int(payment.tax_paise or 0)),
            credit(GST, payment.tax_paise or 0),
        ]
    books.post(
        db,
        f"capture:{payment.id}",
        payment.purpose,
        lines,
        memo=f"भुगतान {payment.public_id[:8]}",
        posted_at=payment.captured_at,
        payment_id=payment.id,
    )
    if payment.fee_paise:
        books.post(
            db,
            f"fee:{payment.id}",
            "provider_fee",
            [debit(EXPENSE_FEES, payment.fee_paise), credit(CASH, payment.fee_paise)],
            memo=f"शुल्क {payment.public_id[:8]}",
            posted_at=payment.captured_at,
            payment_id=payment.id,
        )


def record_refund(db: Session, payment: Payment, refund_paise: int, refund_key: str, when: Optional[datetime] = None) -> int:
    """Give back ``refund_paise`` (never more than is left), split between the writer's share and the
    site's (a tip) or between the price and its GST (a membership) in proportion. Returns what was refunded."""
    gross = int(payment.amount_paise)
    cumulative = min(int(payment.refunded_paise or 0) + int(refund_paise), gross)
    amount = cumulative - int(payment.refunded_paise or 0)
    if amount <= 0:
        return 0
    primary_total = int(payment.writer_share_paise or 0) if payment.purpose == TIP else gross - int(payment.tax_paise or 0)
    primary_to_date = proportional(cumulative, gross, primary_total)
    primary = primary_to_date - int(payment.refunded_primary_paise or 0)
    other = amount - primary
    if payment.purpose == TIP:
        lines = [debit(OWED, primary, payment.target_user_id), debit(INCOME_TIP_FEE, other), credit(CASH, amount)]
    else:
        lines = [debit(INCOME_MEMBERSHIP, primary), debit(GST, other), credit(CASH, amount)]
    books.post(
        db,
        f"refund:{payment.id}:{refund_key}"[:80],
        f"{payment.purpose}_refund",
        lines,
        memo=f"वापसी {payment.public_id[:8]}",
        posted_at=when,
        payment_id=payment.id,
    )
    payment.refunded_paise = cumulative
    payment.refunded_primary_paise = primary_to_date
    payment.status = REFUNDED if cumulative >= gross else PART_REFUNDED
    return amount


def record_pool_month(db: Session, month: EarningMonth, lines: Iterable[EarningLine], admin_id: Optional[int]) -> None:
    """A finalized month: what its pool handed to writers is now owed to them."""
    owed = [(line.user_id, int(line.amount_paise)) for line in lines if line.amount_paise and line.amount_paise > 0]
    total = sum(amount for _, amount in owed)
    if total <= 0:
        return
    books.post(
        db,
        f"pool:{month.id}",
        "pool",
        [debit(POOL, total)] + [credit(OWED, amount, user_id) for user_id, amount in owed],
        memo=f"कोष {month.month}",
        created_by=admin_id,
    )


def record_payout(db: Session, payout: Payout) -> None:
    """Money sent to a writer by hand: what was owed to them goes down, cash goes down."""
    books.post(
        db,
        f"payout:{payout.id}",
        "payout",
        [debit(OWED, payout.amount_paise, payout.user_id), credit(CASH, payout.amount_paise)],
        memo=f"भुगतान {payout.reference}" if payout.reference else "भुगतान",
        posted_at=payout.paid_at,
        created_by=payout.paid_by,
    )


def record_manual(
    db: Session,
    *,
    income: bool,
    account: str,
    amount_paise: int,
    memo: Optional[str],
    posted_at: Optional[datetime],
    admin_id: int,
) -> LedgerTransaction:
    """Income or a cost the owner types in by hand (a sponsor's cheque, the hosting bill)."""
    lines = (
        [debit(CASH, amount_paise), credit(account, amount_paise)]
        if income
        else [debit(account, amount_paise), credit(CASH, amount_paise)]
    )
    return books.post(
        db, f"manual:{uuid.uuid4().hex}", "manual", lines, memo=memo, posted_at=posted_at, created_by=admin_id
    )


def sync_programme(db: Session) -> None:
    """Make sure every finalized month and every payout is in the books.

    New ones are posted the moment they happen; this catches the ones that were recorded before the
    books existed. Safe to run any time (each is posted under its own key). Commits.
    """
    posted = {
        key
        for (key,) in db.query(LedgerTransaction.key).filter(LedgerTransaction.kind.in_(("pool", "payout")))
    }
    for month in db.query(EarningMonth).filter(EarningMonth.finalized_at.isnot(None)):
        if f"pool:{month.id}" not in posted:
            record_pool_month(db, month, month.lines, month.finalized_by)
    for payout in db.query(Payout):
        if f"payout:{payout.id}" not in posted:
            record_payout(db, payout)
    db.commit()
