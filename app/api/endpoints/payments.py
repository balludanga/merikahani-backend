"""Money from readers: tips to a partner writer, and the provider's messages that confirm them.

A reader asks to tip (``POST /tips``); the server fixes the amount, asks the connected provider to set
the payment up, and answers with what the browser needs to take it. Nothing is "paid" until the
provider's signed message arrives at ``POST /webhooks/{provider}``: the browser's say-so is never
believed. The page then watches ``GET /orders/{id}`` until that happens.

With no provider connected (PAYMENTS_PROVIDER empty) everything here answers as if the feature did
not exist, and ``GET /config`` tells the page to show nothing about money.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import membership as members
from app.core.config import settings
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.earnings import current_month, weights_for
from app.core.partner import get_application
from app.core.payments import service
from app.core.payments.providers import get_provider
from app.core.payments.providers.sandbox import SandboxProvider
from app.core.payments.types import PAYMENT_CAPTURED, PAYMENT_FAILED, PAYMENT_REFUNDED, BadWebhook
from app.core.ratelimit import actions, enforce
from app.database import SessionLocal, get_db
from app.models.partner import EarningMonth
from app.models.payments import ERRORED, MEMBERSHIP, PAID_STATUSES, TIP, Payment
from app.models.post import Post
from app.models.user import User
from app.schemas.partner import Person
from app.schemas.payments import (
    CheckoutOut,
    MembershipRules,
    PaymentOut,
    PaymentsConfig,
    PlanOut,
    StartedOut,
    TipReceived,
    TipRequest,
    TipRules,
    TipSettings,
    TipsReceived,
    TipStory,
    TipTarget,
)

logger = logging.getLogger(__name__)
router = APIRouter()

TIP_LIMIT = (20, 60 * 60)  # tips a reader may start per hour
MAX_WEBHOOK_BYTES = 64 * 1024
NOT_A_PARTNER = "यह सुविधा साझेदार लेखकों के लिए है"


def started(payment: Payment, checkout) -> StartedOut:
    return StartedOut(payment=PaymentOut.model_validate(payment), checkout=CheckoutOut(**checkout.__dict__))


def refuse(error: service.PaymentError):
    return HTTPException(status_code=error.status, detail=error.message)


# ---------------------------------------------------------------- what the page asks first

@router.get("/config", response_model=PaymentsConfig)
def payments_config(db: Session = Depends(get_db)):
    """Whether the site takes money at all, and the numbers the tip and membership screens need."""
    provider = get_provider()
    if provider is None:
        return PaymentsConfig(enabled=False)
    low, high = service.tip_limits()
    member_weight, nonmember_weight = weights_for(db.query(EarningMonth).filter(EarningMonth.month == current_month()).first())
    return PaymentsConfig(
        enabled=True,
        test_mode=provider.name == "sandbox",
        provider=provider.name,
        tips=TipRules(min_paise=low, max_paise=high, presets_paise=service.tip_presets(), site_fee_bps=settings.TIP_SITE_FEE_BPS),
        membership=MembershipRules(
            plans=[PlanOut(code=p.code, months=p.months, price_paise=p.price_paise) for p in members.plans().values()],
            gst_included=settings.GST_PERCENT > 0,
            weighted_points=member_weight > nonmember_weight,
        ),
    )


@router.get("/tips/target/{writer_id}", response_model=TipTarget)
def tip_target(
    writer_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Whether this writer can be tipped by the person asking (so the page knows whether to show the button)."""
    if get_provider() is None or (current_user is not None and current_user.id == writer_id):
        return TipTarget(available=False)
    return TipTarget(available=service.can_receive_tips(db, writer_id))


# ---------------------------------------------------------------- tips, from the reader's side

@router.post("/tips", response_model=StartedOut, status_code=201)
def start_tip(
    payload: TipRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce(actions, f"tip:{current_user.id}", *TIP_LIMIT, record=True)
    try:
        payment, checkout = service.start_tip(
            db,
            current_user,
            payload.writer_id,
            int(payload.amount_rupees * 100),
            payload.post_id,
            payload.message,
            payload.anonymous,
        )
    except service.PaymentError as error:
        raise refuse(error)
    return started(payment, checkout)


@router.get("/orders/{public_id}", response_model=PaymentOut)
def order_status(public_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Where one of the reader's own payments stands (the page polls this while the provider confirms)."""
    payment = db.query(Payment).filter(Payment.public_id == public_id, Payment.user_id == current_user.id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="भुगतान नहीं मिला")
    return payment


# ---------------------------------------------------------------- tips, from the writer's side

@router.get("/tips/received", response_model=TipsReceived)
def tips_received(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Every tip sent to the signed-in partner, newest first, with the sender's name unless they kept it back."""
    application = get_application(db, current_user.id)
    if application is None or application.approved_at is None:
        raise HTTPException(status_code=403, detail=NOT_A_PARTNER)
    rows = (
        db.query(Payment)
        .filter(Payment.target_user_id == current_user.id, Payment.purpose == TIP, Payment.status.in_(PAID_STATUSES))
        .order_by(Payment.captured_at.desc(), Payment.id.desc())
        .limit(100)
        .all()
    )
    total = (
        db.query(func.coalesce(func.sum(Payment.writer_share_paise - Payment.refunded_primary_paise), 0))
        .filter(Payment.target_user_id == current_user.id, Payment.purpose == TIP, Payment.status.in_(PAID_STATUSES))
        .scalar()
    )
    stories = {p.id: p for p in db.query(Post).filter(Post.id.in_([r.post_id for r in rows if r.post_id] or [0]))}
    return TipsReceived(
        accepts_tips=application.accepts_tips,
        site_fee_bps=settings.TIP_SITE_FEE_BPS,
        total_paise=int(total or 0),
        count=len(rows),
        items=[
            TipReceived(
                id=row.id,
                amount_paise=row.amount_paise,
                writer_share_paise=row.writer_share_paise or 0,
                refunded_paise=row.refunded_primary_paise or 0,
                at=row.captured_at or row.created_at,
                sender=None if row.anonymous else Person.model_validate(row.payer),
                story=TipStory.model_validate(stories[row.post_id]) if row.post_id in stories else None,
                message=row.message,
            )
            for row in rows
        ],
    )


@router.put("/tips/settings", response_model=TipSettings)
def tip_settings(payload: TipSettings, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A partner switches tips on or off for their own stories."""
    application = get_application(db, current_user.id)
    if application is None or application.approved_at is None:
        raise HTTPException(status_code=403, detail=NOT_A_PARTNER)
    application.accepts_tips = payload.accepts_tips
    db.commit()
    return TipSettings(accepts_tips=application.accepts_tips)


# ---------------------------------------------------------------- the provider's messages

def _work_through(provider, headers: dict, body: bytes) -> dict:
    """Runs on a worker thread with its own database session (the endpoint itself is async, to read the raw body)."""
    db = SessionLocal()
    try:
        return service.handle_webhook(db, provider, headers, body)
    finally:
        db.close()


@router.post("/webhooks/{provider_name}")
async def webhook(provider_name: str, request: Request):
    """Where the connected provider reports what happened. Only the provider currently in use is accepted, and
    nothing in a delivery is believed until its signature over the raw bytes checks out."""
    provider = get_provider()
    if provider is None or provider.name != provider_name.lower():
        raise HTTPException(status_code=404, detail="Not Found")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="संदेश बहुत बड़ा है")  # turned away before any of it is read
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="संदेश बहुत बड़ा है")
    headers = {key.lower(): value for key, value in request.headers.items()}
    try:
        counts = await run_in_threadpool(_work_through, provider, headers, body)
    except service.BadSignature:
        raise HTTPException(status_code=400, detail="हस्ताक्षर सही नहीं है")
    except BadWebhook:
        raise HTTPException(status_code=400, detail="संदेश पहचाना नहीं गया")
    if counts.get(ERRORED):
        # The provider treats a failure as "send it again later", which is what we want for something we could
        # not finish (an event that came before the one it depends on, or a database hiccup).
        return JSONResponse({"ok": False, **counts}, status_code=500)
    return {"ok": True, **counts}


# ---------------------------------------------------------------- the offline pretend provider (development only)

@router.post("/sandbox/{public_id}/{action}", response_model=PaymentOut)
def sandbox_action(
    public_id: str,
    action: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Press "pay", "fail", "refund" or "renew" on the pretend provider's screen. The message it sends goes through
    the very same signed-webhook path a real provider's would. Exists only while DEBUG is on."""
    provider = get_provider()
    if not settings.debug or provider is None or provider.name != "sandbox":
        raise HTTPException(status_code=404, detail="Not Found")
    payment = db.query(Payment).filter(Payment.public_id == public_id, Payment.user_id == current_user.id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="भुगतान नहीं मिला")

    if action == "pay":
        raw, headers = SandboxProvider.delivery(PAYMENT_CAPTURED, payment)
    elif action == "fail":
        raw, headers = SandboxProvider.delivery(PAYMENT_FAILED, payment, reason="टेस्ट में विफल")
    elif action == "refund":
        raw, headers = SandboxProvider.delivery(
            PAYMENT_REFUNDED, payment, refund_id=f"sbx_refund_{payment.public_id}_{payment.refunded_paise}",
            refund_amount=int(payment.amount_paise) - int(payment.refunded_paise or 0),
        )
    elif action == "renew" and payment.purpose == MEMBERSHIP and payment.provider_subscription_id:
        # The next period's charge: no checkout, just a payment on the subscription.
        raw, headers = SandboxProvider.delivery(
            PAYMENT_CAPTURED, payment, reference=None, order_id=None, payment_id=f"sbx_renew_{uuid.uuid4().hex[:12]}",
            subscription_id=payment.provider_subscription_id,
        )
    else:
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        service.handle_webhook(db, provider, headers, raw)
    except (service.BadSignature, BadWebhook):  # cannot happen with our own signature; never hide it if it does
        raise HTTPException(status_code=500, detail="sandbox delivery was rejected")
    db.refresh(payment)
    return payment
