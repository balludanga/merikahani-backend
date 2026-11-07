"""A reader's membership: see it, buy it, stop it renewing, and choose whether the badge shows.

Membership here is patron-style. It supports the site and puts a badge on the reader's profile, and a
member's reactions weigh more in writers' monthly points (when the owner has set it so). It does not lock
any story. Paying goes through the same signed-webhook path as a tip: starting a checkout here only
fixes the plan and the price; the membership begins when the provider confirms the money.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import membership as members
from app.core.dependencies import get_current_user
from app.core.payments import service
from app.core.payments.providers import get_provider
from app.core.ratelimit import actions, enforce
from app.database import get_db
from app.models.payments import Membership
from app.models.user import User
from app.schemas.payments import BadgeRequest, CheckoutOut, MembershipCheckoutRequest, MembershipOut, PaymentOut, StartedOut

logger = logging.getLogger(__name__)
router = APIRouter()

CHECKOUT_LIMIT = (10, 60 * 60)  # memberships a reader may start per hour


def _me(db: Session, user: User) -> MembershipOut:
    return MembershipOut(**members.status_for(db, user.id))


@router.get("/me", response_model=MembershipOut)
def my_membership(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _me(db, current_user)


@router.post("/checkout", response_model=StartedOut, status_code=201)
def start_checkout(
    payload: MembershipCheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce(actions, f"membership:{current_user.id}", *CHECKOUT_LIMIT, record=True)
    try:
        payment, checkout = service.start_membership(db, current_user, payload.plan, payload.auto_renew)
    except service.PaymentError as error:
        raise HTTPException(status_code=error.status, detail=error.message)
    return StartedOut(payment=PaymentOut.model_validate(payment), checkout=CheckoutOut(**checkout.__dict__))


@router.post("/cancel", response_model=MembershipOut)
def cancel(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Stop the membership renewing. The reader stays a member until the time they paid for runs out."""
    state = members.status_for(db, current_user.id)
    if not state["is_member"]:
        raise HTTPException(status_code=400, detail="आपकी कोई चालू सदस्यता नहीं है")
    membership = db.query(Membership).filter(Membership.user_id == current_user.id).first()
    if membership.auto_renew and membership.provider_subscription_id:
        # The provider must really stop charging before we say so: a reader told "cancelled" and then
        # billed again is the worst outcome here.
        provider = get_provider()
        try:
            if provider is None:
                raise RuntimeError("no payment provider is connected")
            provider.cancel_subscription(membership.provider_subscription_id)
        except Exception:  # noqa: BLE001
            logger.exception("could not cancel subscription %s", membership.provider_subscription_id)
            raise HTTPException(status_code=503, detail="सदस्यता रद्द नहीं हो सकी। थोड़ी देर में फिर कोशिश करें")
    members.stop_renewing(db, current_user.id)
    db.commit()
    return _me(db, current_user)


@router.put("/badge", response_model=MembershipOut)
def set_badge(payload: BadgeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Show or hide the member badge on the reader's own profile."""
    membership = db.query(Membership).filter(Membership.user_id == current_user.id).first()
    if membership is None:
        raise HTTPException(status_code=400, detail="आपकी कोई सदस्यता नहीं है")
    membership.show_badge = payload.show
    db.commit()
    return _me(db, current_user)
