"""The owner's side of write and earn: each month's pool, finalizing it, and recording payouts.

How it goes, once a month:

  1. Set the pool for the month (``PUT /months/{month}``) and, if wanted, a ceiling on what one
     point may pay, so a handful of early partners cannot take a big pool for a few claps.
  2. When the month is over, read the preview (``GET /months/{month}``): every partner's claps,
     highlights, responses, how many readers gave them, and how lopsided that is.
  3. Finalize (``POST /months/{month}/finalize``), leaving out anyone whose engagement looks
     bought, with a reason the writer will see. The lines are stored and no longer change.
  4. Pay each partner by UPI yourself and record it (``POST /payouts``). A payout can never be
     larger than what the writer is owed.

Only the review team (ADMIN_EMAILS verified by Google) can reach any of this; everyone else gets
the same 404 a made-up address gets. Nobody can record a payout to themselves.
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.admin import require_admin
from app.core.clock import utcnow
from app.core.config import settings
from app.core.earnings import (
    balance_paise,
    compute_month,
    current_month,
    finalize_month,
    month_has_ended,
    previous_month,
    to_paise,
    valid_month,
)
from app.core.notify import notify
from app.database import get_db
from app.models.notification import PAYOUT_PAID
from app.models.partner import EarningLine, EarningMonth, PartnerApplication, Payout
from app.models.user import User
from app.schemas.partner import (
    BalanceRow,
    FinalizeRequest,
    MonthDetail,
    MonthList,
    MonthRowOut,
    MonthSummary,
    Person,
    PayoutRecord,
    PayoutRequest,
    PoolRequest,
)

router = APIRouter()

BAD_MONTH = "महीना सही नहीं है"
SETTLED = "इस महीने का हिसाब पक्का हो चुका है"


def _month_key(key: str) -> str:
    """A month the owner may work on: well-formed, and not in the future."""
    if not valid_month(key) or key > current_month():
        raise HTTPException(status_code=404, detail=BAD_MONTH)
    return key


def _summary(key: str, row: Optional[EarningMonth]) -> MonthSummary:
    return MonthSummary(
        month=key,
        pool_paise=row.pool_paise if row else 0,
        max_per_point_paise=row.max_per_point_paise if row else None,
        finalized=bool(row and row.finalized_at),
        finalized_at=row.finalized_at if row else None,
        ended=month_has_ended(key),
        total_points=row.total_points if row else 0,
        distributed_paise=row.distributed_paise if row else 0,
    )


def _people(db: Session, ids: List[int]) -> Dict[int, User]:
    return {user.id: user for user in db.query(User).filter(User.id.in_(ids or [0]))}


def _detail(db: Session, key: str, row: Optional[EarningMonth], excluded: Optional[Dict[int, str]] = None) -> MonthDetail:
    summary = _summary(key, row)
    if row is not None and row.finalized_at is not None:
        lines = db.query(EarningLine).filter(EarningLine.month_id == row.id).all()
        people = _people(db, [line.user_id for line in lines])
        rows = [
            MonthRowOut(
                user=Person.model_validate(people[line.user_id]),
                claps=line.claps,
                highlights=line.highlights,
                responses=line.responses,
                readers=line.readers,
                points=line.points,
                amount_paise=line.amount_paise,
                held_reason=line.held_reason,
            )
            for line in sorted(lines, key=lambda l: (-l.amount_paise, -l.points, l.user_id))
            if line.user_id in people
        ]
        return MonthDetail(**summary.model_dump(), preview=False, rows=rows)

    computed = compute_month(
        db,
        key,
        row.pool_paise if row else 0,
        row.max_per_point_paise if row else None,
        excluded,
    )
    people = _people(db, [item.tally.user_id for item in computed])
    rows = [
        MonthRowOut(
            user=Person.model_validate(people[item.tally.user_id]),
            claps=item.tally.claps,
            highlights=item.tally.highlights,
            responses=item.tally.responses,
            readers=item.tally.readers,
            points=item.tally.points,
            amount_paise=item.amount_paise,
            held_reason=item.held_reason,
            top_reader_pct=item.tally.top_reader_pct,
        )
        for item in computed
        if item.tally.user_id in people
    ]
    return MonthDetail(**summary.model_dump(), preview=True, rows=rows)


# ---------------------------------------------------------------- months

@router.get("/months", response_model=MonthList)
def months(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """This month, last month (so it can be finalized) and every month that has a pool or a result."""
    stored = {row.month: row for row in db.query(EarningMonth).order_by(EarningMonth.month.desc()).limit(60)}
    now = current_month()
    keys = set(stored) | {now, previous_month(now)}
    return MonthList(current=now, months=[_summary(key, stored.get(key)) for key in sorted(keys, reverse=True)])


@router.get("/months/{month}", response_model=MonthDetail)
def month_detail(
    month: str,
    exclude: str = Query("", max_length=400),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """A month's result if it is final, otherwise a preview of what finalizing it now would store.

    ``exclude`` (comma-separated user ids) shows the preview with those writers left out.
    """
    key = _month_key(month)
    row = db.query(EarningMonth).filter(EarningMonth.month == key).first()
    left_out = {int(part): "—" for part in exclude.split(",") if part.strip().isdigit()}
    return _detail(db, key, row, left_out)


@router.put("/months/{month}", response_model=MonthDetail)
def set_pool(
    month: str,
    payload: PoolRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Set (or change) the money to be shared for a month that is not final yet."""
    key = _month_key(month)
    row = db.query(EarningMonth).filter(EarningMonth.month == key).with_for_update().first()
    if row is not None and row.finalized_at is not None:
        raise HTTPException(status_code=400, detail=SETTLED)
    if row is None:
        row = EarningMonth(month=key)
        db.add(row)
    row.pool_paise = to_paise(payload.pool_rupees)
    row.max_per_point_paise = to_paise(payload.max_per_point_rupees) if payload.max_per_point_rupees is not None else None
    db.commit()
    db.refresh(row)
    return _detail(db, key, row)


@router.post("/months/{month}/finalize", response_model=MonthDetail)
def finalize(
    month: str,
    payload: FinalizeRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Store the month's result. After this the pool, the points and the shares no longer change."""
    key = _month_key(month)
    row = db.query(EarningMonth).filter(EarningMonth.month == key).with_for_update().first()
    if row is None:
        raise HTTPException(status_code=400, detail="पहले इस महीने का कोष तय करें")
    if row.finalized_at is not None:
        raise HTTPException(status_code=400, detail=SETTLED)
    if not month_has_ended(key):
        raise HTTPException(status_code=400, detail="यह महीना अभी पूरा नहीं हुआ है")
    finalize_month(db, row, admin.id, {item.user_id: item.reason for item in payload.exclude})
    db.commit()
    db.refresh(row)
    return _detail(db, key, row)


# ---------------------------------------------------------------- money owed and paid

def _balance_rows(db: Session, only: Optional[int] = None) -> List[BalanceRow]:
    query = (
        db.query(PartnerApplication)
        .options(selectinload(PartnerApplication.user))
        .filter(PartnerApplication.approved_at.isnot(None))
    )
    if only is not None:
        query = query.filter(PartnerApplication.user_id == only)
    applications = query.all()
    if not applications:
        return []
    ids = [a.user_id for a in applications]
    earned = dict(
        db.query(EarningLine.user_id, func.sum(EarningLine.amount_paise))
        .filter(EarningLine.user_id.in_(ids))
        .group_by(EarningLine.user_id)
        .all()
    )
    paid = {
        user_id: (int(total or 0), last)
        for user_id, total, last in db.query(Payout.user_id, func.sum(Payout.amount_paise), func.max(Payout.paid_at))
        .filter(Payout.user_id.in_(ids))
        .group_by(Payout.user_id)
        .all()
    }
    minimum = settings.PARTNER_MIN_PAYOUT_RUPEES * 100
    rows = []
    for application in applications:
        total_earned = int(earned.get(application.user_id) or 0)
        total_paid, last_paid = paid.get(application.user_id, (0, None))
        balance = total_earned - total_paid
        rows.append(
            BalanceRow(
                user=Person.model_validate(application.user),
                balance_paise=balance,
                earned_paise=total_earned,
                paid_paise=total_paid,
                payout_upi=application.payout_upi,
                due=balance >= minimum and balance > 0,
                last_paid_at=last_paid,
            )
        )
    rows.sort(key=lambda r: (-r.balance_paise, r.user.id))
    return rows


@router.get("/balances", response_model=List[BalanceRow])
def balances(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Every partner (and former partner) with what they are owed, biggest first."""
    return _balance_rows(db)


def _rupees(paise: int) -> str:
    whole, rest = divmod(paise, 100)
    return f"₹{whole}" if rest == 0 else f"₹{whole}.{rest:02d}"


@router.post("/payouts", response_model=BalanceRow, status_code=201)
def record_payout(
    payload: PayoutRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Note that money was sent. It can never exceed what the writer is owed."""
    if payload.user_id == admin.id:
        raise HTTPException(status_code=400, detail="अपने खाते का भुगतान आप स्वयं दर्ज नहीं कर सकते")
    # Two payouts recorded at once must not both pass the balance check: lock the writer's row.
    application = (
        db.query(PartnerApplication)
        .filter(PartnerApplication.user_id == payload.user_id, PartnerApplication.approved_at.isnot(None))
        .with_for_update()
        .first()
    )
    if application is None:
        raise HTTPException(status_code=404, detail="साझेदार लेखक नहीं मिला")
    amount = to_paise(payload.amount_rupees)
    owed = balance_paise(db, payload.user_id)
    if amount > owed:
        raise HTTPException(
            status_code=400, detail=f"भुगतान की राशि बकाया राशि ({_rupees(max(owed, 0))}) से अधिक नहीं हो सकती"
        )
    db.add(Payout(user_id=payload.user_id, amount_paise=amount, reference=payload.reference, paid_by=admin.id, paid_at=utcnow()))
    db.commit()
    text = _rupees(amount) + (f" · {payload.reference}" if payload.reference else "")
    notify(db, actor_id=admin.id, kind=PAYOUT_PAID, recipient_id=payload.user_id, text=text)
    return _balance_rows(db, only=payload.user_id)[0]


@router.get("/payouts", response_model=List[PayoutRecord])
def payouts(
    user_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Recorded payouts, newest first (for one writer, or everyone)."""
    query = db.query(Payout).options(selectinload(Payout.user))
    if user_id is not None:
        query = query.filter(Payout.user_id == user_id)
    return [
        PayoutRecord(
            id=p.id,
            amount_paise=p.amount_paise,
            reference=p.reference,
            paid_at=p.paid_at,
            user=Person.model_validate(p.user),
        )
        for p in query.order_by(Payout.paid_at.desc(), Payout.id.desc()).limit(limit).all()
    ]
