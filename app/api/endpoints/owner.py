"""The owner's money pages: the monthly sheet that suggests the pool, the books, and the payment log.

Only an owner (OWNER_EMAILS, verified by Google) can reach any of this. A reviewer who is not an owner
gets the same 404 a made-up address gets, so these pages do not reveal that they exist. Nothing on them
is ever part of a response a writer or a reader can read.
"""

from datetime import datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core import ledger as books
from app.core.admin import require_owner
from app.core.clock import utcnow
from app.core.earnings import IST, current_month, month_bounds, month_has_ended, to_paise, valid_month
from app.core.payments import accounting
from app.core.payments.providers import PROVIDERS, get_provider
from app.core.profit import sheet_for
from app.database import get_db
from app.models.ledger import LedgerTransaction, ProfitSheet
from app.models.partner import EarningMonth
from app.models.payments import ERRORED, REJECTED, Payment, PaymentEvent
from app.models.user import User
from app.schemas.owner import (
    BooksOut,
    ChoiceRequest,
    EntryOut,
    EventRow,
    ManualEntryRequest,
    PaymentRow,
    PaymentsHealth,
    SheetChoice,
    SheetOut,
    TransactionOut,
)
from app.schemas.partner import Person

router = APIRouter()

BAD_MONTH = "महीना सही नहीं है"
SETTLED = "इस महीने का हिसाब पक्का हो चुका है"


def _month(key: str) -> str:
    """A month the owner may look at: well-formed and not in the future."""
    if not valid_month(key) or key > current_month():
        raise HTTPException(status_code=404, detail=BAD_MONTH)
    return key


def _sheet_out(db: Session, month: str) -> SheetOut:
    accounting.sync_programme(db)
    sheet = sheet_for(db, month)
    return SheetOut(
        month=month,
        ended=month_has_ended(month),
        income=sheet.income,
        costs=sheet.costs,
        income_total=sheet.income_total,
        cost_total=sheet.cost_total,
        profit=sheet.profit,
        tips_gross=sheet.tips_gross,
        tips_to_writers=sheet.tips_to_writers,
        gst_collected=sheet.gst_collected,
        yearly_received=sheet.yearly_received,
        choice=SheetChoice(
            pool_percent=sheet.choice.pool_percent,
            holdback_percent=sheet.choice.holdback_percent,
            note=sheet.choice.note,
            own=sheet.choice.own,
        ),
        carried_in=sheet.carried_in,
        pool_before_holdback=sheet.pool_before_holdback,
        held_back=sheet.held_back,
        suggested=sheet.suggested,
        pool_set=sheet.pool_set,
        finalized=sheet.finalized,
        cash_paise=books.balance(db, books.CASH),
        owed_paise=books.balance(db, books.OWED),
        gst_payable_paise=books.balance(db, books.GST),
        balanced=books.is_balanced(db),
    )


# ---------------------------------------------------------------- the monthly sheet

@router.get("/profit/{month}", response_model=SheetOut)
def profit_sheet(month: str, db: Session = Depends(get_db), _owner: User = Depends(require_owner)):
    return _sheet_out(db, _month(month))


@router.put("/profit/{month}", response_model=SheetOut)
def choose(month: str, payload: ChoiceRequest, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    """Record this month's percent for the pool, how much of it to hold back, and a note to self."""
    key = _month(month)
    row = db.query(ProfitSheet).filter(ProfitSheet.month == key).first()
    if row is None:
        row = ProfitSheet(month=key)
        db.add(row)
    row.pool_percent = payload.pool_percent
    row.holdback_percent = payload.holdback_percent
    row.note = payload.note
    row.updated_by = owner.id
    db.commit()
    return _sheet_out(db, key)


@router.post("/profit/{month}/apply", response_model=SheetOut)
def apply_suggestion(month: str, db: Session = Depends(get_db), _owner: User = Depends(require_owner)):
    """Put the suggestion into the month's pool. It changes only the pool amount: the ceiling per point and the
    weights the reviewers set in the desk are left as they are."""
    key = _month(month)
    suggested = sheet_for(db, key).suggested
    if suggested is None:
        raise HTTPException(status_code=400, detail="पहले कोष का प्रतिशत तय करें")
    row = db.query(EarningMonth).filter(EarningMonth.month == key).with_for_update().first()
    if row is not None and row.finalized_at is not None:
        raise HTTPException(status_code=400, detail=SETTLED)
    if row is None:
        row = EarningMonth(month=key)
        db.add(row)
    row.pool_paise = suggested
    db.commit()
    return _sheet_out(db, key)


# ---------------------------------------------------------------- the books

def _transaction_out(txn: LedgerTransaction, reversed_ids: set) -> TransactionOut:
    return TransactionOut(
        id=txn.id,
        key=txn.key,
        kind=txn.kind,
        memo=txn.memo,
        posted_at=txn.posted_at,
        reverses_id=txn.reverses_id,
        reversed=txn.id in reversed_ids,
        entries=[EntryOut(account=e.account, amount_paise=e.amount_paise, user_id=e.user_id) for e in txn.entries],
    )


def _books(db: Session, month: str) -> BooksOut:
    start, end = month_bounds(month)
    rows = (
        db.query(LedgerTransaction)
        .options(selectinload(LedgerTransaction.entries))
        .filter(LedgerTransaction.posted_at >= start, LedgerTransaction.posted_at < end)
        .order_by(LedgerTransaction.posted_at.desc(), LedgerTransaction.id.desc())
        .limit(300)
        .all()
    )
    reversed_ids = {rid for (rid,) in db.query(LedgerTransaction.reverses_id).filter(LedgerTransaction.reverses_id.isnot(None))}
    return BooksOut(
        month=month,
        balanced=books.is_balanced(db),
        cash_paise=books.balance(db, books.CASH),
        owed_paise=books.balance(db, books.OWED),
        gst_payable_paise=books.balance(db, books.GST),
        items=[_transaction_out(txn, reversed_ids) for txn in rows],
    )


@router.get("/books", response_model=BooksOut)
def the_books(
    month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _owner: User = Depends(require_owner),
):
    """Every transaction that counts in a month (default: this one), newest first."""
    accounting.sync_programme(db)
    return _books(db, _month(month or current_month()))


@router.post("/books/entries", response_model=TransactionOut, status_code=201)
def add_entry(payload: ManualEntryRequest, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    """Type in money that did not come through the payment provider (a sponsor's payment, the hosting bill)."""
    if payload.account in books.MANUAL_INCOME:
        income = True
    elif payload.account in books.MANUAL_EXPENSE:
        income = False
    else:
        raise HTTPException(status_code=400, detail="यह मद चुनी नहीं जा सकती")
    today = (utcnow() + IST).date()
    day = payload.on or today
    if day > today:
        raise HTTPException(status_code=400, detail="आगे की तारीख नहीं डाली जा सकती")
    if not valid_month(f"{day.year:04d}-{day.month:02d}"):
        raise HTTPException(status_code=400, detail="तारीख सही नहीं है")
    # Noon, Indian time, on that day: safely inside the right month whichever way the clock is read.
    posted_at = datetime.combine(day, time(12, 0)) - IST
    txn = accounting.record_manual(
        db,
        income=income,
        account=payload.account,
        amount_paise=to_paise(payload.amount_rupees),
        memo=payload.memo,
        posted_at=posted_at,
        admin_id=owner.id,
    )
    db.commit()
    db.refresh(txn)
    return _transaction_out(txn, set())


@router.post("/books/{txn_id}/reverse", response_model=TransactionOut)
def reverse_entry(txn_id: int, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    """Undo an entry the owner typed in, by writing its mirror image (nothing is ever deleted).

    Only hand-typed entries can be reversed here: a payment, a refund or a payout is undone by the event
    that really happens (a refund from the provider), never by editing the books behind its back."""
    txn = db.query(LedgerTransaction).filter(LedgerTransaction.id == txn_id).first()
    if txn is None:
        raise HTTPException(status_code=404, detail="प्रविष्टि नहीं मिली")
    if txn.kind != "manual":
        raise HTTPException(status_code=400, detail="केवल हाथ से डाली गई प्रविष्टि उलटी जा सकती है")
    if db.query(LedgerTransaction.id).filter(LedgerTransaction.reverses_id == txn.id).first():
        raise HTTPException(status_code=400, detail="यह प्रविष्टि पहले ही उलटी जा चुकी है")
    mirror = books.reverse(db, txn, owner.id)
    db.commit()
    db.refresh(mirror)
    return _transaction_out(mirror, set())


# ---------------------------------------------------------------- the payment log

@router.get("/payments", response_model=PaymentsHealth)
def payments_log(db: Session = Depends(get_db), _owner: User = Depends(require_owner)):
    """Whether payments are connected, how many payments are in each state, the deliveries that went wrong,
    and the latest payments."""
    provider = get_provider()
    counts = dict(db.query(Payment.status, func.count(Payment.id)).group_by(Payment.status).all())
    problems = (
        db.query(PaymentEvent)
        .filter(PaymentEvent.status.in_((ERRORED, REJECTED)))
        .order_by(PaymentEvent.received_at.desc(), PaymentEvent.id.desc())
        .limit(20)
        .all()
    )
    recent = (
        db.query(Payment)
        .options(selectinload(Payment.payer), selectinload(Payment.target))
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(30)
        .all()
    )
    return PaymentsHealth(
        provider=provider.name if provider else None,
        connected=provider is not None,
        test_mode=bool(provider and provider.name == "sandbox"),
        counts={status: int(n) for status, n in counts.items()},
        problems=[EventRow(id=e.id, type=e.type, status=e.status, detail=e.detail, received_at=e.received_at) for e in problems],
        recent=[
            PaymentRow(
                public_id=p.public_id,
                purpose=p.purpose,
                status=p.status,
                amount_paise=p.amount_paise,
                fee_paise=p.fee_paise,
                refunded_paise=p.refunded_paise or 0,
                created_at=p.created_at,
                captured_at=p.captured_at,
                payer=Person.model_validate(p.payer),
                target=Person.model_validate(p.target) if p.target is not None else None,
                failure_reason=p.failure_reason,
            )
            for p in recent
        ],
    )
