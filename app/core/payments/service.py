"""Taking money: starting a tip or a membership payment, and turning the provider's messages into the books.

Two rules run through all of it.

  1. The browser never says what was paid. The server decides the price when the payment is made,
     and only a message from the provider that passes its signature check (the webhook) can mark the
     payment as paid. Even then the amount in the message must match what was asked for.
  2. Every message can arrive twice, late, or out of order, and must do the right thing each time.
     Each one is logged under the provider's own id; a repeat that already worked changes nothing, and
     one that failed half way is worked through again.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Mapping, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import membership as members
from app.core.clock import utcnow
from app.core.config import settings
from app.core.notify import notify
from app.core.partner import get_application
from app.core.payments import accounting
from app.core.payments.money import split_gst, tip_split
from app.core.payments.providers import get_provider
from app.core.payments.providers.base import PaymentProvider
from app.core.payments.types import (
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    PAYMENT_REFUNDED,
    SUBSCRIPTION_CANCELLED,
    Checkout,
    CheckoutRequest,
    ProviderEvent,
)
from app.models.notification import TIP_RECEIVED
from app.models.partner import APPROVED
from app.models.payments import (
    APPLIED,
    CAPTURED,
    CREATED,
    ERRORED,
    FAILED,
    IGNORED,
    MEMBERSHIP,
    PAID_STATUSES,
    REFUNDED,
    REJECTED,
    RECEIVED,
    TIP,
    Membership,
    Payment,
    PaymentEvent,
)
from app.models.post import Post
from app.models.user import User

logger = logging.getLogger(__name__)

CURRENCY = "INR"
OFF = "भुगतान की सुविधा अभी चालू नहीं है"
NOT_STARTED = "भुगतान शुरू नहीं हो सका। थोड़ी देर में फिर कोशिश करें"
PAID = PAID_STATUSES


class PaymentError(Exception):
    """Something to tell the person, in Hindi, with the HTTP status to send it under."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class BadSignature(Exception):
    """A delivery whose signature does not check out. Nothing in it is believed."""


class NotYet(Exception):
    """An event that refers to something we have not recorded yet (it arrived out of order). The
    provider is asked to send it again later."""


def _need_provider() -> PaymentProvider:
    provider = get_provider()
    if provider is None:
        raise PaymentError(OFF, 503)
    return provider


# --------------------------------------------------------------------------- tips

def tip_limits() -> Tuple[int, int]:
    return settings.TIP_MIN_RUPEES * 100, settings.TIP_MAX_RUPEES * 100


def tip_presets() -> List[int]:
    """The quick-pick amounts, in paise, kept inside the limits."""
    low, high = tip_limits()
    picks = []
    for part in settings.TIP_PRESET_RUPEES.split(","):
        part = part.strip()
        if part.isdigit() and low <= int(part) * 100 <= high:
            picks.append(int(part) * 100)
    return picks


def can_receive_tips(db: Session, writer_id: int) -> bool:
    """A partner writer who has not switched tips off."""
    application = get_application(db, writer_id)
    return bool(application and application.status == APPROVED and application.accepts_tips)


def _begin(db: Session, provider: PaymentProvider, payment: Payment, request: CheckoutRequest) -> Tuple[Payment, Checkout]:
    """Save the payment, ask the provider to set it up, and keep what it answered."""
    db.add(payment)
    db.flush()
    try:
        checkout = provider.create_checkout(request)
    except Exception:  # noqa: BLE001 - whatever the provider does, the reader gets one plain sentence
        db.rollback()
        logger.exception("%s could not start a %s payment", provider.name, payment.purpose)
        raise PaymentError(NOT_STARTED, 503)
    payment.provider_order_id = checkout.order_id
    payment.provider_subscription_id = checkout.subscription_id
    db.commit()
    db.refresh(payment)
    return payment, checkout


def start_tip(
    db: Session,
    payer: User,
    writer_id: int,
    amount_paise: int,
    post_id: Optional[int] = None,
    message: Optional[str] = None,
    anonymous: bool = False,
) -> Tuple[Payment, Checkout]:
    provider = _need_provider()
    low, high = tip_limits()
    if not low <= amount_paise <= high:
        raise PaymentError(f"समर्थन की राशि ₹{low // 100} से ₹{high // 100} के बीच होनी चाहिए")
    if writer_id == payer.id:
        raise PaymentError("अपनी ही रचनाओं को समर्थन नहीं भेजा जा सकता")
    if not can_receive_tips(db, writer_id):
        raise PaymentError("यह लेखक अभी समर्थन नहीं ले रहे हैं", 404)
    if post_id is not None and not db.query(Post.id).filter(Post.id == post_id, Post.author_id == writer_id, Post.published == 1).first():
        raise PaymentError("रचना नहीं मिली", 404)
    payment = Payment(
        public_id=uuid.uuid4().hex,
        user_id=payer.id,
        purpose=TIP,
        status=CREATED,
        amount_paise=amount_paise,
        currency=CURRENCY,
        provider=provider.name,
        target_user_id=writer_id,
        post_id=post_id,
        message=message,
        anonymous=anonymous,
    )
    request = CheckoutRequest(
        reference=payment.public_id,
        amount_paise=amount_paise,
        currency=CURRENCY,
        purpose=TIP,
        description="Lekhanii tip",
        payer_name=payer.full_name or payer.username,
        payer_email=payer.email,
    )
    return _begin(db, provider, payment, request)


# --------------------------------------------------------------------------- memberships

def start_membership(db: Session, user: User, plan_code: str, auto_renew: bool) -> Tuple[Payment, Checkout]:
    provider = _need_provider()
    plan = members.plans().get(plan_code)
    if plan is None:
        raise PaymentError("यह योजना नहीं मिली", 404)
    if auto_renew and members.status_for(db, user.id)["auto_renew"]:
        # A second subscription would replace the first on our side while the provider kept charging it, and its
        # charges could then no longer be matched to anyone. Extra time can still be bought without renewing.
        raise PaymentError("आपकी सदस्यता पहले से अपने-आप आगे बढ़ती है। अलग से नवीनीकरण शुरू करने की ज़रूरत नहीं।", 400)
    payment = Payment(
        public_id=uuid.uuid4().hex,
        user_id=user.id,
        purpose=MEMBERSHIP,
        status=CREATED,
        amount_paise=plan.price_paise,
        currency=CURRENCY,
        provider=provider.name,
        plan_code=plan.code,
    )
    request = CheckoutRequest(
        reference=payment.public_id,
        amount_paise=plan.price_paise,
        currency=CURRENCY,
        purpose=MEMBERSHIP,
        description="Lekhanii membership",
        recurring=auto_renew,
        months=plan.months,
        payer_name=user.full_name or user.username,
        payer_email=user.email,
    )
    return _begin(db, provider, payment, request)


# --------------------------------------------------------------------------- the provider's messages

def handle_webhook(db: Session, provider: PaymentProvider, headers: Mapping[str, str], body: bytes) -> dict:
    """Verify one delivery, then work through each event in it. Raises ``BadSignature`` for a delivery that
    is not genuinely the provider's and ``BadWebhook`` for one that is not well-formed."""
    if not provider.verify_webhook(headers, body):
        raise BadSignature()
    counts = {APPLIED: 0, IGNORED: 0, REJECTED: 0, ERRORED: 0}
    for event in provider.parse_webhook(headers, body):
        counts[process_event(db, provider.name, event)] += 1
    return counts


def _log(db: Session, provider_name: str, event: ProviderEvent) -> PaymentEvent:
    """Store the delivery (once) before touching anything, so even a crash leaves a trace."""
    row = db.query(PaymentEvent).filter(PaymentEvent.provider == provider_name, PaymentEvent.event_id == event.id[:120]).first()
    if row is None:
        row = PaymentEvent(
            provider=provider_name,
            event_id=event.id[:120],
            type=event.type[:40],
            payload=json.dumps(event.log, ensure_ascii=False, default=str)[:4000],
            status=RECEIVED,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:  # the same delivery, handled in another request a moment ago
            row = db.query(PaymentEvent).filter(PaymentEvent.provider == provider_name, PaymentEvent.event_id == event.id[:120]).one()
    db.commit()
    return row


def process_event(db: Session, provider_name: str, event: ProviderEvent) -> str:
    """Apply one event. Returns what became of it: applied, ignored, rejected or errored."""
    row = _log(db, provider_name, event)
    if row.status in (APPLIED, IGNORED, REJECTED):
        return IGNORED  # a repeat of something already dealt with
    row_id = row.id
    try:
        status, detail, payment = _apply(db, provider_name, event)
        row = db.query(PaymentEvent).filter(PaymentEvent.id == row_id).one()
        row.status, row.detail, row.processed_at = status, detail[:300], utcnow()
        row.payment_id = payment.id if payment is not None else None
        db.commit()
    except Exception as exc:  # noqa: BLE001 - recorded, and the provider is told to try again
        db.rollback()
        if not isinstance(exc, NotYet):
            logger.exception("could not apply %s event %s", provider_name, event.id)
        row = db.query(PaymentEvent).filter(PaymentEvent.id == row_id).one()
        row.status, row.processed_at = ERRORED, utcnow()
        row.detail = (str(exc) if isinstance(exc, NotYet) else "आंतरिक त्रुटि")[:300]
        db.commit()
        return ERRORED
    if status == APPLIED and event.type == PAYMENT_CAPTURED and payment is not None and payment.purpose == TIP:
        _tell_writer(db, payment)
    return status


def _tell_writer(db: Session, payment: Payment) -> None:
    """The bell says a tip arrived (the site speaks, so an anonymous tipper's name is never part of it)."""
    notify(
        db,
        actor_id=payment.user_id,
        kind=TIP_RECEIVED,
        recipient_id=payment.target_user_id,
        text=payment.message,
        count=int(payment.writer_share_paise or 0),
    )


def _find_payment(db: Session, provider_name: str, event: ProviderEvent) -> Optional[Payment]:
    """The payment an event is about, locked for the rest of this transaction."""
    base = db.query(Payment).filter(Payment.provider == provider_name)
    for column, value in (
        (Payment.public_id, event.reference),
        (Payment.provider_order_id, event.order_id),
        (Payment.provider_payment_id, event.payment_id),
    ):
        if value:
            payment = base.filter(column == value).with_for_update().first()
            if payment is not None:
                return payment
    return None


def _apply(db: Session, provider_name: str, event: ProviderEvent) -> Tuple[str, str, Optional[Payment]]:
    if event.type == PAYMENT_CAPTURED:
        return _on_captured(db, provider_name, event)
    if event.type == PAYMENT_FAILED:
        return _on_failed(db, provider_name, event)
    if event.type == PAYMENT_REFUNDED:
        return _on_refunded(db, provider_name, event)
    if event.type == SUBSCRIPTION_CANCELLED:
        return _on_subscription_cancelled(db, event)
    return IGNORED, "इस तरह की सूचना का हम उपयोग नहीं करते", None


def _renewal_payment(db: Session, provider_name: str, event: ProviderEvent) -> Optional[Payment]:
    """A self-renewing membership charged again: there is no checkout for it, so make the payment now.

    The amount it must match is what THIS subscription has been charging (its last paid payment), not today's
    price list: a subscription keeps the price it was started at, so a later change to the plan's price must not
    make the provider's next charge look wrong and leave the member charged without time (the plan's price is
    only the fallback for a subscription we have no payment for)."""
    membership = db.query(Membership).filter(Membership.provider_subscription_id == event.subscription_id).first()
    plan = members.plans().get(membership.plan_code) if membership else None
    if membership is None or plan is None:
        return None
    last = (
        db.query(Payment)
        .filter(
            Payment.provider == provider_name,
            Payment.provider_subscription_id == event.subscription_id,
            Payment.status.in_(PAID),
        )
        .order_by(Payment.id.desc())
        .first()
    )
    payment = Payment(
        public_id=uuid.uuid4().hex,
        user_id=membership.user_id,
        purpose=MEMBERSHIP,
        status=CREATED,
        amount_paise=last.amount_paise if last is not None else plan.price_paise,
        currency=CURRENCY,
        provider=provider_name,
        plan_code=plan.code,
        provider_subscription_id=event.subscription_id,
    )
    db.add(payment)
    db.flush()
    return payment


def _on_captured(db: Session, provider_name: str, event: ProviderEvent) -> Tuple[str, str, Optional[Payment]]:
    payment = _find_payment(db, provider_name, event)
    if payment is None and event.subscription_id:
        payment = _renewal_payment(db, provider_name, event)
    if payment is None:
        return IGNORED, "यह भुगतान हमारे यहाँ दर्ज नहीं है", None
    if payment.status in PAID:
        return IGNORED, "यह भुगतान पहले ही दर्ज हो चुका है", payment
    # The money that arrived must be exactly the money we asked for, in the currency we asked in.
    if event.amount_paise is None or event.amount_paise != payment.amount_paise:
        payment.failure_reason = "राशि मेल नहीं खाती"
        return REJECTED, f"राशि मेल नहीं खाती: माँगी {payment.amount_paise}, मिली {event.amount_paise}", payment
    if (event.currency or CURRENCY).upper() != payment.currency:
        payment.failure_reason = "मुद्रा मेल नहीं खाती"
        return REJECTED, f"मुद्रा मेल नहीं खाती: {event.currency}", payment

    now = utcnow()
    payment.status = CAPTURED
    payment.captured_at = event.occurred_at or now
    payment.failure_reason = None
    payment.provider_payment_id = event.payment_id or payment.provider_payment_id
    payment.provider_order_id = payment.provider_order_id or event.order_id
    payment.fee_paise = event.fee_paise
    if payment.purpose == TIP:
        payment.writer_share_paise, payment.site_fee_paise = tip_split(payment.amount_paise, settings.TIP_SITE_FEE_BPS)
    else:
        _net, payment.tax_paise = split_gst(payment.amount_paise, settings.GST_PERCENT)
    db.flush()
    accounting.record_capture(db, payment)
    if payment.purpose == MEMBERSHIP:
        members.grant_period(db, payment, members.plans()[payment.plan_code].months, payment.captured_at)
    return APPLIED, "भुगतान दर्ज हुआ", payment


def _on_failed(db: Session, provider_name: str, event: ProviderEvent) -> Tuple[str, str, Optional[Payment]]:
    payment = _find_payment(db, provider_name, event)
    if payment is None:
        return IGNORED, "यह भुगतान हमारे यहाँ दर्ज नहीं है", None
    if payment.status not in (CREATED, FAILED):
        return IGNORED, "भुगतान पहले ही पूरा हो चुका है", payment  # a failed attempt arriving after a good one
    payment.status = FAILED
    payment.failure_reason = (event.reason or "भुगतान विफल रहा")[:200]
    return APPLIED, "विफल दर्ज हुआ", payment


def _on_refunded(db: Session, provider_name: str, event: ProviderEvent) -> Tuple[str, str, Optional[Payment]]:
    payment = _find_payment(db, provider_name, event)
    if payment is None:
        return IGNORED, "यह भुगतान हमारे यहाँ दर्ज नहीं है", None
    if payment.status == CREATED or payment.status == FAILED:
        raise NotYet("वापसी का संदेश भुगतान के संदेश से पहले आ गया")
    if event.refund_paise is None or event.refund_paise <= 0:
        return REJECTED, "वापसी की राशि नहीं बताई गई", payment
    refunded = accounting.record_refund(db, payment, event.refund_paise, event.refund_id or event.id, event.occurred_at)
    if refunded <= 0:
        return IGNORED, "पूरी राशि पहले ही लौट चुकी है", payment
    if payment.status == REFUNDED and payment.purpose == MEMBERSHIP:
        members.void_period(db, payment)
    return APPLIED, f"वापसी दर्ज हुई: {refunded}", payment


def _on_subscription_cancelled(db: Session, event: ProviderEvent) -> Tuple[str, str, Optional[Payment]]:
    membership = (
        db.query(Membership).filter(Membership.provider_subscription_id == event.subscription_id).first()
        if event.subscription_id
        else None
    )
    if membership is None:
        return IGNORED, "यह सदस्यता हमारे यहाँ दर्ज नहीं है", None
    members.stop_renewing(db, membership.user_id, event.occurred_at)
    return APPLIED, "नवीनीकरण बंद हुआ", None


# --------------------------------------------------------------------------- reading it back

def paid_payment(db: Session, public_id: str) -> Optional[Payment]:
    return db.query(Payment).filter(Payment.public_id == public_id, Payment.status.in_(PAID)).first()
