"""The owner's money pages: the monthly sheet, the books and the payment log. Amounts are whole paise."""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.partner import Person
from app.schemas.profile import _clean


class SheetChoice(BaseModel):
    pool_percent: Optional[int] = None
    holdback_percent: int = 0
    note: Optional[str] = None
    # Typed for this month (False: carried over from the last month that had one).
    own: bool = False


class SheetOut(BaseModel):
    month: str
    ended: bool
    income: Dict[str, int]
    costs: Dict[str, int]
    income_total: int
    cost_total: int
    profit: int
    tips_gross: int
    tips_to_writers: int
    gst_collected: int
    yearly_received: int
    choice: SheetChoice
    carried_in: int
    pool_before_holdback: int
    held_back: int
    # None until a percent has been chosen.
    suggested: Optional[int] = None
    # What the pool is set to in the reviewers' desk right now.
    pool_set: Optional[int] = None
    finalized: bool
    # Money the books hold at this moment, and whether they add up.
    cash_paise: int
    owed_paise: int
    gst_payable_paise: int
    balanced: bool


class ChoiceRequest(BaseModel):
    # Empty clears it (no suggestion).
    pool_percent: Optional[int] = Field(None, ge=0, le=100)
    holdback_percent: int = Field(0, ge=0, le=50)
    note: Optional[str] = None

    @field_validator("note")
    @classmethod
    def tidy(cls, value: Optional[str]) -> Optional[str]:
        cleaned = _clean(value, multiline=True)
        return cleaned[:500] if cleaned else None


class ManualEntryRequest(BaseModel):
    # One of the income or cost accounts the owner may type in.
    account: str = Field(max_length=32)
    amount_rupees: Decimal = Field(gt=0, le=10_000_000, max_digits=12, decimal_places=2)
    memo: Optional[str] = None
    # The day it belongs to, in Indian time (today if empty).
    on: Optional[date] = None

    @field_validator("memo")
    @classmethod
    def tidy(cls, value: Optional[str]) -> Optional[str]:
        cleaned = _clean(value)
        return cleaned[:200] if cleaned else None


class EntryOut(BaseModel):
    account: str
    amount_paise: int
    user_id: Optional[int] = None


class TransactionOut(BaseModel):
    id: int
    key: str
    kind: str
    memo: Optional[str] = None
    posted_at: datetime
    reverses_id: Optional[int] = None
    reversed: bool = False
    entries: List[EntryOut]


class BooksOut(BaseModel):
    month: str
    balanced: bool
    cash_paise: int
    owed_paise: int
    gst_payable_paise: int
    items: List[TransactionOut]


class PaymentRow(BaseModel):
    public_id: str
    purpose: str
    status: str
    amount_paise: int
    fee_paise: Optional[int] = None
    refunded_paise: int
    created_at: datetime
    captured_at: Optional[datetime] = None
    payer: Person
    target: Optional[Person] = None
    failure_reason: Optional[str] = None


class EventRow(BaseModel):
    id: int
    type: str
    status: str
    detail: Optional[str] = None
    received_at: datetime


class PaymentsHealth(BaseModel):
    provider: Optional[str] = None
    connected: bool
    test_mode: bool
    counts: Dict[str, int]
    # Deliveries that were refused or that failed on our side: each needs a look.
    problems: List[EventRow]
    recent: List[PaymentRow]
