from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.clock import utcnow
from app.database import Base

# Where a writer stands with the write-and-earn programme (partner_applications.status).
PENDING = "pending"      # applied; waiting for the review team
APPROVED = "approved"    # a partner: their stories earn a share of each month's pool
DECLINED = "declined"    # not accepted this time; may apply again after a wait
WITHDRAWN = "withdrawn"  # the writer took the application back
REVOKED = "revoked"      # removed by the review team; only the team can bring them back


class PartnerApplication(Base):
    """One row per writer: their application and, once approved, their membership.

    A later application (after a decline or a withdrawal) reuses the row, so a writer is never in
    the programme twice and the unique key settles a race between two taps.
    """

    __tablename__ = "partner_applications"
    __table_args__ = (UniqueConstraint("user_id", name="uq_partner_user"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(12), nullable=False, default=PENDING, index=True)
    # The writer's own words to the review team.
    note = Column(Text, nullable=True)
    # What the numbers were when they applied (the review team also sees the live ones).
    followers_at_apply = Column(Integer, nullable=False, default=0)
    stories_at_apply = Column(Integer, nullable=False, default=0)
    attempts = Column(Integer, nullable=False, default=1)
    applied_at = Column(DateTime, nullable=False, default=utcnow)
    decided_at = Column(DateTime, nullable=True)
    # The reviewer's account is never shown to the writer: the team speaks, not a person.
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Why it was declined or revoked; shown to the writer as written.
    decision_note = Column(Text, nullable=True)
    # When the current membership began. Engagement before this never counts towards a pool.
    approved_at = Column(DateTime, nullable=True)
    # Where to pay. Private: only the writer and the review team ever read it.
    payout_upi = Column(String(80), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User", foreign_keys=[user_id])


class EarningMonth(Base):
    """One calendar month (Indian time) of the pool: how much money there is, and once the month
    is finalized, how it was shared. Amounts are whole paise."""

    __tablename__ = "earning_months"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(String(7), nullable=False, unique=True)  # "2026-10"
    pool_paise = Column(Integer, nullable=False, default=0)
    # Ceiling on what one point can pay, so a tiny early community cannot drain a big pool.
    max_per_point_paise = Column(Integer, nullable=True)
    finalized_at = Column(DateTime, nullable=True)
    finalized_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Frozen when the month is finalized.
    total_points = Column(Integer, nullable=False, default=0)
    distributed_paise = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    lines = relationship("EarningLine", back_populates="month_row", cascade="all, delete-orphan")


class EarningLine(Base):
    """What one partner earned in one finalized month, with the engagement behind it."""

    __tablename__ = "earning_lines"
    __table_args__ = (UniqueConstraint("month_id", "user_id", name="uq_earning_month_user"),)

    id = Column(Integer, primary_key=True, index=True)
    month_id = Column(Integer, ForeignKey("earning_months.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    claps = Column(Integer, nullable=False, default=0)
    highlights = Column(Integer, nullable=False, default=0)
    responses = Column(Integer, nullable=False, default=0)
    readers = Column(Integer, nullable=False, default=0)
    points = Column(Integer, nullable=False, default=0)
    amount_paise = Column(Integer, nullable=False, default=0)
    # Set when the review team left this writer out of the month; shown to the writer.
    held_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    month_row = relationship("EarningMonth", back_populates="lines")
    user = relationship("User", foreign_keys=[user_id])


class Payout(Base):
    """Money actually sent to a partner (by hand, through UPI), recorded by the review team."""

    __tablename__ = "payouts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount_paise = Column(Integer, nullable=False)
    # The UPI transaction reference, for the writer's and the owner's records.
    reference = Column(String(80), nullable=True)
    paid_at = Column(DateTime, default=utcnow, nullable=False)
    paid_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
