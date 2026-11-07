from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.clock import utcnow
from app.database import Base


class LedgerTransaction(Base):
    """One thing that happened with money, written down once.

    Its entries always add up to zero (see app.core.ledger). ``key`` names the event ("capture:12",
    "payout:3"), so posting the same event twice is harmless. Transactions are never edited or
    deleted: a mistake is undone by a second transaction that reverses the first.
    """

    __tablename__ = "ledger_transactions"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(80), nullable=False, unique=True)
    kind = Column(String(24), nullable=False, index=True)
    memo = Column(String(200), nullable=True)
    # When it counts, for the month it belongs to (naive UTC like every timestamp here).
    posted_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reverses_id = Column(Integer, ForeignKey("ledger_transactions.id"), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    entries = relationship("LedgerEntry", back_populates="transaction", order_by="LedgerEntry.id")


class LedgerEntry(Base):
    """One line of a transaction: an amount moved into (+) or out of (-) one account."""

    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, index=True)
    txn_id = Column(Integer, ForeignKey("ledger_transactions.id"), nullable=False, index=True)
    account = Column(String(32), nullable=False, index=True)
    # Whole paise. Debits are positive and credits negative, so a transaction sums to zero.
    amount_paise = Column(BigInteger, nullable=False)
    # Whose money, for the one account that is kept per writer (what is owed to them).
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    transaction = relationship("LedgerTransaction", back_populates="entries")


class ProfitSheet(Base):
    """What the owner decided for one month's sheet. The figures themselves are never stored: they
    are always worked out from the books, so the sheet cannot disagree with them."""

    __tablename__ = "profit_sheets"
    __table_args__ = (UniqueConstraint("month", name="uq_profit_sheet_month"),)

    id = Column(Integer, primary_key=True, index=True)
    month = Column(String(7), nullable=False)  # "2026-10"
    # Whole percent of the month's profit that goes into the pool; empty until the owner sets it.
    pool_percent = Column(Integer, nullable=True)
    # Whole percent of that pool kept back until next month (for refunds that arrive late).
    holdback_percent = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
