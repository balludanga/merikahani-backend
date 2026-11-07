"""Write and earn: what the writer sends and sees, what the review team sends and sees.

Money crosses the API as whole paise (``*_paise``) coming out and as rupees with at most two
decimals going in (``*_rupees``); the page does the formatting.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.profile import _clean

# name@bank. A UPI address is Latin by nature (9876543210@ybl, name.surname@okaxis).
_UPI = re.compile(r"^[A-Za-z0-9._\-]{2,50}@[A-Za-z][A-Za-z0-9.\-]{1,29}$")


# ---------------------------------------------------------------- the writer

class ApplyRequest(BaseModel):
    note: Optional[str] = None
    agree: bool = False

    @field_validator("note")
    @classmethod
    def tidy(cls, value: Optional[str]) -> Optional[str]:
        cleaned = _clean(value, multiline=True)
        return cleaned[:1000] if cleaned else None


class UpiRequest(BaseModel):
    # Empty clears it.
    upi_id: Optional[str] = None

    @field_validator("upi_id")
    @classmethod
    def valid(cls, value: Optional[str]) -> Optional[str]:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        if not _UPI.match(cleaned):
            raise ValueError("यूपीआई (UPI) आईडी सही नहीं लगती — सही रूप: नाम@बैंक (name@bank)")
        return cleaned


class RequirementOut(BaseModel):
    have: int
    need: int
    met: bool


class RulesOut(BaseModel):
    """The numbers behind the points, so the page quotes the same ones the server uses."""

    min_followers: int
    min_stories: int
    clap_points: int
    highlight_points: int
    max_highlights: int
    response_points: int
    max_responses: int
    reader_min_age_days: int
    reapply_days: int
    min_payout_paise: int
    # What a reader's reaction is worth this month, by whether the reader holds a membership.
    member_weight: int = 1
    nonmember_weight: int = 1


class ApplicationOut(BaseModel):
    status: str
    applied_at: datetime
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    approved_at: Optional[datetime] = None
    attempts: int
    note: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PartnerStatus(BaseModel):
    # none | pending | approved | declined | withdrawn | revoked
    status: str
    followers: RequirementOut
    stories: RequirementOut
    can_apply: bool
    # Why not, in Hindi, when can_apply is False (the same sentence the apply call would answer with).
    blocked: Optional[str] = None
    reapply_on: Optional[datetime] = None
    application: Optional[ApplicationOut] = None
    # The writer's own payout address; never part of any public profile.
    payout_upi: Optional[str] = None
    rules: RulesOut


class LiveMonth(BaseModel):
    month: str
    claps: int
    highlights: int
    responses: int
    readers: int
    points: int
    member_points: int = 0
    # What the points would pay if the month ended now; None until a pool is set for it.
    estimate_paise: Optional[int] = None
    ends_at: datetime


class StatementLine(BaseModel):
    month: str
    claps: int
    highlights: int
    responses: int
    readers: int
    points: int
    member_points: int = 0
    amount_paise: int
    held_reason: Optional[str] = None


class PayoutOut(BaseModel):
    id: int
    amount_paise: int
    reference: Optional[str] = None
    paid_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EarningsOut(BaseModel):
    balance_paise: int
    # Everything credited to the writer: the pool's shares and tips together.
    earned_paise: int
    pool_paise: int = 0
    tips_paise: int = 0
    paid_paise: int
    live: Optional[LiveMonth] = None
    months: List[StatementLine]
    payouts: List[PayoutOut]


# ---------------------------------------------------------------- the review team

class Person(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class QueueCounts(BaseModel):
    pending: int
    approved: int
    declined: int
    revoked: int


class QueueItem(BaseModel):
    id: int
    user: Person
    status: str
    applied_at: datetime
    decided_at: Optional[datetime] = None
    attempts: int
    followers_now: int
    followers_at_apply: int
    stories_now: int
    note: Optional[str] = None


class Signals(BaseModel):
    followers: int
    followers_verified: int
    followers_new: int
    followers_inactive: int
    followers_mutual: int
    followers_week: int
    followers_month: int
    stories: int
    latest_story_at: Optional[datetime] = None
    claps_total: int
    responses_received: int
    account_days: int


class TopStory(BaseModel):
    id: int
    title: str
    slug: str
    claps: int


class ApplicationDetail(QueueItem):
    decision_note: Optional[str] = None
    approved_at: Optional[datetime] = None
    bio: Optional[str] = None
    tagline: Optional[str] = None
    member_since: Optional[datetime] = None
    payout_upi: Optional[str] = None
    signals: Signals
    top_stories: List[TopStory]


class Decision(BaseModel):
    """The reason a writer is shown when they are declined or removed."""

    reason: str

    @field_validator("reason")
    @classmethod
    def written(cls, value: str) -> str:
        cleaned = _clean(value, multiline=True) or ""
        if len(cleaned) < 5:
            raise ValueError("कारण कम से कम 5 अक्षर का लिखें")
        return cleaned[:1000]


# ---------------------------------------------------------------- the pool and the payouts

class PoolRequest(BaseModel):
    pool_rupees: Decimal = Field(ge=0, le=10_000_000, max_digits=12, decimal_places=2)
    # What one point may pay at most; empty means no ceiling.
    max_per_point_rupees: Optional[Decimal] = Field(None, gt=0, le=100_000, max_digits=9, decimal_places=2)
    # What a reader's reaction is worth this month; empty means the site-wide setting.
    member_weight: Optional[int] = Field(None, ge=1, le=10)
    nonmember_weight: Optional[int] = Field(None, ge=0, le=10)


class Exclusion(BaseModel):
    user_id: int
    reason: str

    @field_validator("reason")
    @classmethod
    def written(cls, value: str) -> str:
        cleaned = _clean(value) or ""
        if len(cleaned) < 3:
            raise ValueError("रोकने का कारण लिखें")
        return cleaned[:300]


class FinalizeRequest(BaseModel):
    exclude: List[Exclusion] = Field(default_factory=list, max_length=200)


class PayoutRequest(BaseModel):
    user_id: int
    amount_rupees: Decimal = Field(gt=0, le=10_000_000, max_digits=12, decimal_places=2)
    reference: Optional[str] = Field(None, max_length=80)

    @field_validator("reference")
    @classmethod
    def tidy(cls, value: Optional[str]) -> Optional[str]:
        return _clean(value)


class MonthSummary(BaseModel):
    month: str
    pool_paise: int
    max_per_point_paise: Optional[int] = None
    finalized: bool
    finalized_at: Optional[datetime] = None
    ended: bool
    total_points: int = 0
    distributed_paise: int = 0
    member_weight: int = 1
    nonmember_weight: int = 1


class MonthRowOut(BaseModel):
    user: Person
    claps: int
    highlights: int
    responses: int
    readers: int
    points: int
    member_points: int = 0
    amount_paise: int
    held_reason: Optional[str] = None
    # The biggest single reader's share of this writer's points, in percent: a lopsided number
    # (one account giving nearly everything) is the thing to look at before finalizing.
    top_reader_pct: int = 0


class MonthDetail(MonthSummary):
    # True while the month is open: the rows are a preview, not yet stored.
    preview: bool
    rows: List[MonthRowOut]


class MonthList(BaseModel):
    current: str
    months: List[MonthSummary]


class BalanceRow(BaseModel):
    user: Person
    balance_paise: int
    earned_paise: int
    # Of the earned amount, the part that came from tips.
    tips_paise: int = 0
    paid_paise: int
    payout_upi: Optional[str] = None
    # Balance has reached the smallest amount that is paid out.
    due: bool
    last_paid_at: Optional[datetime] = None


class PayoutRecord(PayoutOut):
    user: Person
