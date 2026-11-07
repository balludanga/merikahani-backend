"""The books: every rupee that moves is written down once, in entries that balance.

A *transaction* is a set of *entries* whose amounts add up to zero. A debit is a positive amount
and a credit a negative one, so "money came in" is written as cash up and income (or a debt to
a writer) up by the same amount. Because every transaction balances, the whole ledger always
sums to zero, and that is checked (``is_balanced``) rather than hoped for.

Rules the rest of the code relies on:

  * Amounts are whole paise. Nothing here ever touches a float.
  * A transaction has a ``key`` naming the event it records ("capture:12", "payout:3"). Posting
    the same key again returns the first one untouched, so a repeated webhook or a retried
    request cannot count money twice.
  * Nothing is edited or deleted. A mistake is undone by ``reverse``, a second transaction that
    mirrors the first and is itself kept.
  * ``post`` does not commit. The caller commits, so the books change in the same database
    transaction as whatever caused them (a payment turning captured, a payout being recorded).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models.ledger import LedgerEntry, LedgerTransaction

DEBIT = "debit"
CREDIT = "credit"


@dataclass(frozen=True)
class Account:
    code: str
    # Which side makes it bigger: assets and expenses grow with debits, the rest with credits.
    grows_with: str
    # Kept per person (the writer it is owed to).
    per_user: bool = False


# Money the business holds, wherever it sits (the payment provider's balance and the bank together).
CASH = "cash"
# What the site owes each writer: their tips and their pool earnings, less what was paid out.
OWED = "owed_to_writers"
# GST collected inside membership prices and not yet paid over.
GST = "gst_payable"
# What the monthly pool hands to writers. Not a cost of running the site, so the owner's sheet leaves it out.
POOL = "writer_pool"

INCOME_MEMBERSHIP = "income_membership"
INCOME_TIP_FEE = "income_tip_fee"
INCOME_SPONSOR = "income_sponsor"
INCOME_GRANT = "income_grant"
INCOME_OTHER = "income_other"

EXPENSE_FEES = "expense_provider_fee"
EXPENSE_HOSTING = "expense_hosting"
EXPENSE_NARRATION = "expense_narration"
EXPENSE_ACCOUNTANT = "expense_accountant"
EXPENSE_DOMAIN = "expense_domain"
EXPENSE_OTHER = "expense_other"

# Income the owner types in by hand (money that did not come through the payment provider).
MANUAL_INCOME = (INCOME_SPONSOR, INCOME_GRANT, INCOME_OTHER)
# Costs the owner types in by hand. Payment-provider fees are recorded by the payment code.
MANUAL_EXPENSE = (EXPENSE_HOSTING, EXPENSE_NARRATION, EXPENSE_ACCOUNTANT, EXPENSE_DOMAIN, EXPENSE_OTHER)
INCOME = (INCOME_MEMBERSHIP, INCOME_TIP_FEE) + MANUAL_INCOME
EXPENSE = (EXPENSE_FEES,) + MANUAL_EXPENSE

ACCOUNTS: Dict[str, Account] = {
    account.code: account
    for account in (
        Account(CASH, DEBIT),
        Account(OWED, CREDIT, per_user=True),
        Account(GST, CREDIT),
        Account(POOL, DEBIT),
        *(Account(code, CREDIT) for code in INCOME),
        *(Account(code, DEBIT) for code in EXPENSE),
    )
}


class Unbalanced(ValueError):
    """The entries of a transaction do not add up to zero (a bug in the caller, never a user error)."""


class KeyReused(ValueError):
    """A key that already names a transaction was posted again with different entries. Returning the first
    one quietly would drop money, so this stops instead (a bug in the caller, never a user error)."""


@dataclass(frozen=True)
class Line:
    account: str
    amount: int  # signed paise: debit +, credit -
    user_id: Optional[int] = None


def debit(account: str, amount: int, user_id: Optional[int] = None) -> Line:
    return Line(account, abs(int(amount)), user_id)


def credit(account: str, amount: int, user_id: Optional[int] = None) -> Line:
    return Line(account, -abs(int(amount)), user_id)


def natural(account: str, signed_sum: int) -> int:
    """A signed sum read the way people read that account: positive when it holds what it should hold."""
    return signed_sum if ACCOUNTS[account].grows_with == DEBIT else -signed_sum


def _check(lines: Iterable[Line]) -> List[Line]:
    kept = [line for line in lines if line.amount != 0]
    if not kept:
        raise Unbalanced("a transaction needs at least one non-zero entry")
    for line in kept:
        account = ACCOUNTS.get(line.account)
        if account is None:
            raise Unbalanced(f"unknown account {line.account!r}")
        if account.per_user and line.user_id is None:
            raise Unbalanced(f"{line.account} entries must name the writer")
    if sum(line.amount for line in kept) != 0:
        raise Unbalanced(f"entries add up to {sum(line.amount for line in kept)}, not zero")
    return kept


def _shape(entries) -> List[Tuple[str, int, Optional[int]]]:
    return sorted((e.account, int(e.amount_paise), e.user_id) for e in entries)


def post(
    db: Session,
    key: str,
    kind: str,
    lines: Iterable[Line],
    *,
    memo: Optional[str] = None,
    posted_at: Optional[datetime] = None,
    created_by: Optional[int] = None,
    payment_id: Optional[int] = None,
    reverses_id: Optional[int] = None,
) -> LedgerTransaction:
    """Write one transaction, or return the one already written under ``key``. Does not commit."""
    kept = _check(lines)
    existing = db.query(LedgerTransaction).filter(LedgerTransaction.key == key).first()
    if existing is not None:
        return _same_or_stop(existing, kept)
    transaction = LedgerTransaction(
        key=key,
        kind=kind,
        memo=memo[:200] if memo else None,
        posted_at=posted_at or utcnow(),
        payment_id=payment_id,
        created_by=created_by,
        reverses_id=reverses_id,
    )
    transaction.entries = [LedgerEntry(account=l.account, amount_paise=l.amount, user_id=l.user_id) for l in kept]
    try:
        # A savepoint, so losing a race for the same key leaves the caller's own work intact.
        with db.begin_nested():
            db.add(transaction)
            db.flush()
    except IntegrityError:
        existing = db.query(LedgerTransaction).filter(LedgerTransaction.key == key).first()
        if existing is None:
            raise
        return _same_or_stop(existing, kept)
    return transaction


def _same_or_stop(existing: LedgerTransaction, lines: List[Line]) -> LedgerTransaction:
    if _shape(existing.entries) != sorted((l.account, l.amount, l.user_id) for l in lines):
        raise KeyReused(f"{existing.key!r} is already in the books with different entries")
    return existing


def reverse(db: Session, transaction: LedgerTransaction, admin_id: Optional[int], memo: Optional[str] = None) -> LedgerTransaction:
    """Undo a transaction by writing its mirror image. Reversing twice returns the first reversal.

    The mirror counts in the SAME period as the original, because the point is to correct that period's
    figures (a bill typed in wrongly for November must come off November's sheet, not appear as a credit in
    December)."""
    lines = [Line(e.account, -e.amount_paise, e.user_id) for e in transaction.entries]
    return post(
        db,
        f"reverse:{transaction.id}",
        "reversal",
        lines,
        memo=memo or f"उलटा: {transaction.memo or transaction.key}",
        posted_at=transaction.posted_at,
        created_by=admin_id,
        payment_id=transaction.payment_id,
        reverses_id=transaction.id,
    )


# --------------------------------------------------------------------------- reading the books

def _in_period(query, start: Optional[datetime], end: Optional[datetime]):
    if start is not None:
        query = query.filter(LedgerTransaction.posted_at >= start)
    if end is not None:
        query = query.filter(LedgerTransaction.posted_at < end)
    return query


def balance(
    db: Session,
    account: str,
    *,
    user_id: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> int:
    """What ``account`` holds (naturally signed), optionally for one writer and/or one period."""
    query = db.query(func.coalesce(func.sum(LedgerEntry.amount_paise), 0)).join(
        LedgerTransaction, LedgerTransaction.id == LedgerEntry.txn_id
    ).filter(LedgerEntry.account == account)
    if user_id is not None:
        query = query.filter(LedgerEntry.user_id == user_id)
    return natural(account, int(_in_period(query, start, end).scalar() or 0))


def totals_by_account(db: Session, start: Optional[datetime] = None, end: Optional[datetime] = None) -> Dict[str, int]:
    """Every account's naturally signed total for a period (accounts with nothing are left out)."""
    query = db.query(LedgerEntry.account, func.sum(LedgerEntry.amount_paise)).join(
        LedgerTransaction, LedgerTransaction.id == LedgerEntry.txn_id
    )
    rows = _in_period(query, start, end).group_by(LedgerEntry.account).all()
    return {account: natural(account, int(total or 0)) for account, total in rows if account in ACCOUNTS and total}


def is_balanced(db: Session) -> bool:
    """The whole ledger sums to zero, and so does every single transaction in it."""
    if int(db.query(func.coalesce(func.sum(LedgerEntry.amount_paise), 0)).scalar() or 0) != 0:
        return False
    off = (
        db.query(LedgerEntry.txn_id)
        .group_by(LedgerEntry.txn_id)
        .having(func.sum(LedgerEntry.amount_paise) != 0)
        .first()
    )
    return off is None


def owed_to_each(db: Session) -> Dict[int, int]:
    """What the books say each writer is owed, by writer id (zero balances left out)."""
    rows = (
        db.query(LedgerEntry.user_id, func.sum(LedgerEntry.amount_paise))
        .filter(LedgerEntry.account == OWED, LedgerEntry.user_id.isnot(None))
        .group_by(LedgerEntry.user_id)
        .all()
    )
    return {user_id: -int(total) for user_id, total in rows if total}


def split_by_kind(db: Session, start: datetime, end: datetime) -> Tuple[int, int]:
    """(income, costs) over a period, from the income and expense accounts alone."""
    totals = totals_by_account(db, start, end)
    return sum(totals.get(code, 0) for code in INCOME), sum(totals.get(code, 0) for code in EXPENSE)
