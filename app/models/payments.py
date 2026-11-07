from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, true
from sqlalchemy.orm import relationship

from app.core.clock import utcnow
from app.database import Base

# What a payment is for (payments.purpose).
TIP = "tip"
MEMBERSHIP = "membership"

# Where a payment stands (payments.status). Only CAPTURED means the money arrived.
CREATED = "created"      # the checkout exists; nothing paid yet
CAPTURED = "captured"    # paid: the books and the writer's balance have it
FAILED = "failed"        # the provider said the attempt failed (a later attempt may still succeed)
REFUNDED = "refunded"    # all of it went back to the payer
PART_REFUNDED = "part_refunded"
# The states in which the money really arrived (a refund comes after a capture).
PAID_STATUSES = (CAPTURED, PART_REFUNDED, REFUNDED)

# What became of a webhook delivery (payment_events.status).
RECEIVED = "received"    # stored, not yet worked through (also: worked through and failed, retried later)
APPLIED = "applied"      # it changed something
IGNORED = "ignored"      # understood, nothing to do (an event for a payment we do not know, a repeat)
REJECTED = "rejected"    # understood but refused: the money did not match what we asked for
ERRORED = "errored"      # our own code failed on it; the provider will deliver it again


class Payment(Base):
    """One attempt to take money from one reader: a tip to a writer, or a membership period.

    The row is made BEFORE the reader pays, with the price decided by the server, and only a
    signed message from the provider (never the browser) can turn it into CAPTURED.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_order_id", name="uq_payment_provider_order"),
        UniqueConstraint("provider", "provider_payment_id", name="uq_payment_provider_payment"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # The unguessable reference we hand to the provider and the browser.
    public_id = Column(String(32), nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # who pays
    purpose = Column(String(12), nullable=False)
    status = Column(String(14), nullable=False, default=CREATED, index=True)
    amount_paise = Column(BigInteger, nullable=False)
    currency = Column(String(3), nullable=False, default="INR")
    provider = Column(String(20), nullable=False)
    provider_order_id = Column(String(80), nullable=True)
    provider_payment_id = Column(String(80), nullable=True)
    provider_subscription_id = Column(String(80), nullable=True, index=True)

    # A tip: who it is for, which story it came from, what the reader wrote, and whether their name is kept back.
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="SET NULL"), nullable=True)
    message = Column(String(200), nullable=True)
    anonymous = Column(Boolean, nullable=False, default=False)
    # A membership: which plan.
    plan_code = Column(String(16), nullable=True)

    # Worked out when the money arrives. For a tip: the writer's share and the site's. For a
    # membership: the GST that sits inside the price.
    writer_share_paise = Column(BigInteger, nullable=True)
    site_fee_paise = Column(BigInteger, nullable=True)
    tax_paise = Column(BigInteger, nullable=False, default=0)
    # What the provider kept, when it says.
    fee_paise = Column(BigInteger, nullable=True)
    refunded_paise = Column(BigInteger, nullable=False, default=0)
    # The part of the refunds that came out of the writer's share (a tip) or of the net price (a membership).
    refunded_primary_paise = Column(BigInteger, nullable=False, default=0)
    failure_reason = Column(String(200), nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    captured_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    payer = relationship("User", foreign_keys=[user_id])
    target = relationship("User", foreign_keys=[target_user_id])
    post = relationship("Post", foreign_keys=[post_id])


class PaymentEvent(Base):
    """A delivery from the provider, kept so a repeat is recognised and so a problem can be traced.

    Only what the code needs is stored (ids, amounts, status): never the payer's phone, email,
    card or UPI details, which the provider's raw message may carry.
    """

    __tablename__ = "payment_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_payment_event"),)

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(20), nullable=False)
    event_id = Column(String(120), nullable=False)
    type = Column(String(40), nullable=False)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(10), nullable=False, default=RECEIVED, index=True)
    detail = Column(String(300), nullable=True)
    payload = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    processed_at = Column(DateTime, nullable=True)


# Where a membership stands (memberships.status).
ACTIVE = "active"        # paid up, and will renew on its own
CANCELLED = "cancelled"  # will not renew; still a member until the period ends
LAPSED = "lapsed"        # the paid time ran out


class Membership(Base):
    """A reader's subscription as a whole. One row per reader; the paid time itself is in the periods."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", name="uq_membership_user"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_code = Column(String(16), nullable=False)
    status = Column(String(10), nullable=False, default=ACTIVE)
    auto_renew = Column(Boolean, nullable=False, default=False)
    provider_subscription_id = Column(String(80), nullable=True, index=True)
    # Whether the badge shows on the reader's public profile (their choice).
    show_badge = Column(Boolean, nullable=False, default=True, server_default=true())
    started_at = Column(DateTime, nullable=False, default=utcnow)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    user = relationship("User", foreign_keys=[user_id])


class MembershipPeriod(Base):
    """One stretch of paid time, from one payment. Whether a reader was a member at a moment is
    answered here, so a past month can always be recomputed the way it was."""

    __tablename__ = "membership_periods"
    __table_args__ = (UniqueConstraint("payment_id", name="uq_period_payment"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=False)
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    # A refund that returns the whole price takes the period back: it never counted.
    voided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
