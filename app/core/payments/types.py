"""The plain shapes that cross between our code and a payment provider.

Providers speak their own language (order ids, payment entities, signatures). Each adapter
translates that into these few types and back, so nothing outside ``providers/`` knows which
provider is connected. Money is whole paise; times are naive UTC like everything else here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

# The normalised event types an adapter may emit (anything else it must drop before returning).
PAYMENT_CAPTURED = "payment.captured"
PAYMENT_FAILED = "payment.failed"
PAYMENT_REFUNDED = "payment.refunded"
SUBSCRIPTION_CANCELLED = "subscription.cancelled"
EVENT_TYPES = (PAYMENT_CAPTURED, PAYMENT_FAILED, PAYMENT_REFUNDED, SUBSCRIPTION_CANCELLED)


@dataclass
class CheckoutRequest:
    """What we ask a provider to set up before the reader pays."""

    reference: str            # our Payment.public_id; the provider must echo it back on every event
    amount_paise: int
    currency: str
    purpose: str              # "tip" | "membership"
    description: str          # short, shown on the provider's payment screen
    recurring: bool = False   # a membership that renews itself
    months: int = 1           # length of a recurring period
    payer_name: Optional[str] = None
    payer_email: Optional[str] = None


@dataclass
class Checkout:
    """What the browser needs to take the payment."""

    provider: str
    order_id: str
    subscription_id: Optional[str] = None
    # Public, provider-specific values for the browser's payment widget (never a secret).
    client: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderEvent:
    """One thing the provider tells us happened, in our words."""

    id: str                                   # the provider's own id for this delivery; repeats share it
    type: str                                 # one of EVENT_TYPES
    reference: Optional[str] = None           # our Payment.public_id, when the provider carries it
    order_id: Optional[str] = None
    payment_id: Optional[str] = None
    subscription_id: Optional[str] = None
    amount_paise: Optional[int] = None
    currency: Optional[str] = None
    fee_paise: Optional[int] = None           # what the provider kept, when it says
    refund_id: Optional[str] = None
    refund_paise: Optional[int] = None
    reason: Optional[str] = None
    occurred_at: Optional[datetime] = None
    # Only what the code uses, for the log. Never the payer's phone, email, card or UPI details.
    log: Dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """The provider could not do what we asked (network, rejected request, not connected)."""


class NotConnected(ProviderError):
    """An adapter whose connection has not been written yet (the skeletons)."""


class BadWebhook(Exception):
    """A delivery that is not a well-formed message from the provider."""
