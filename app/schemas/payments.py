"""Money in and out of the API. Amounts leave as whole paise (``*_paise``) and come in as rupees with
at most two decimals (``*_rupees``); the page does the formatting."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.partner import Person
from app.schemas.profile import _clean


# ---------------------------------------------------------------- what the page asks first

class TipRules(BaseModel):
    min_paise: int
    max_paise: int
    presets_paise: List[int]
    # What the site keeps of each tip, in basis points (1000 = 10%); the writer gets the rest.
    site_fee_bps: int


class PlanOut(BaseModel):
    code: str
    months: int
    price_paise: int


class MembershipRules(BaseModel):
    plans: List[PlanOut]
    # The price already includes GST.
    gst_included: bool
    # Whether a member's reactions are currently worth more than other readers' (so the page may say so).
    weighted_points: bool


class PaymentsConfig(BaseModel):
    enabled: bool
    # The offline pretend provider is in use: nothing real is charged.
    test_mode: bool = False
    provider: Optional[str] = None
    tips: Optional[TipRules] = None
    membership: Optional[MembershipRules] = None


# ---------------------------------------------------------------- starting a payment

class TipRequest(BaseModel):
    writer_id: int
    amount_rupees: Decimal = Field(gt=0, le=1_000_000, max_digits=9, decimal_places=2)
    post_id: Optional[int] = None
    message: Optional[str] = None
    # Keep the sender's name from the writer. (The site always knows, for refunds.)
    anonymous: bool = False

    @field_validator("message")
    @classmethod
    def tidy(cls, value: Optional[str]) -> Optional[str]:
        cleaned = _clean(value)
        return cleaned[:200] if cleaned else None


class MembershipCheckoutRequest(BaseModel):
    plan: str = Field(max_length=16)
    auto_renew: bool = True


class PaymentOut(BaseModel):
    public_id: str
    purpose: str
    status: str
    amount_paise: int
    currency: str
    created_at: datetime
    captured_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CheckoutOut(BaseModel):
    provider: str
    order_id: str
    subscription_id: Optional[str] = None
    client: Dict[str, Any] = Field(default_factory=dict)


class StartedOut(BaseModel):
    payment: PaymentOut
    checkout: CheckoutOut


# ---------------------------------------------------------------- tips, from the writer's side

class TipTarget(BaseModel):
    available: bool


class TipStory(BaseModel):
    id: int
    title: str
    slug: str

    model_config = ConfigDict(from_attributes=True)


class TipReceived(BaseModel):
    id: int
    # What the reader paid, the writer's share of it, and what was later refunded out of that share.
    amount_paise: int
    writer_share_paise: int
    refunded_paise: int
    at: datetime
    # None when the sender kept their name back.
    sender: Optional[Person] = None
    story: Optional[TipStory] = None
    message: Optional[str] = None


class TipsReceived(BaseModel):
    accepts_tips: bool
    site_fee_bps: int
    # The writer's share of all tips so far, after refunds.
    total_paise: int
    count: int
    items: List[TipReceived]


class TipSettings(BaseModel):
    accepts_tips: bool


# ---------------------------------------------------------------- membership

class MembershipOut(BaseModel):
    is_member: bool
    ever_member: bool
    plan_code: Optional[str] = None
    paid_until: Optional[datetime] = None
    auto_renew: bool
    cancelled: bool
    show_badge: bool
    since: Optional[datetime] = None


class BadgeRequest(BaseModel):
    show: bool
