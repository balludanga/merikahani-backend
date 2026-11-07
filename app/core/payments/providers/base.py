"""What every payment provider's adapter must do. Nothing else in the code talks to a provider."""

from abc import ABC, abstractmethod
from typing import List, Mapping

from app.core.payments.types import Checkout, CheckoutRequest, NotConnected, ProviderEvent


class PaymentProvider(ABC):
    #: The name used in PAYMENTS_PROVIDER, in the webhook URL and in payments.provider.
    name: str = ""

    @abstractmethod
    def configured(self) -> bool:
        """True only when this adapter is fully connected and has its credentials. A provider that
        says False is treated as absent: the site shows no payment features at all."""

    @abstractmethod
    def create_checkout(self, request: CheckoutRequest) -> Checkout:
        """Set up one payment (or one subscription) with the provider and say what the browser needs.
        Must carry ``request.reference`` so every later event can be tied back to our Payment."""

    @abstractmethod
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Check the provider's signature over the RAW body, in constant time. Header names arrive
        lower-cased. Never trust a delivery that fails this."""

    @abstractmethod
    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> List[ProviderEvent]:
        """Turn a verified delivery into our events, dropping the ones we do not use. Raise
        ``BadWebhook`` if the body is not what the provider sends."""

    def cancel_subscription(self, subscription_id: str) -> None:
        """Stop a subscription from charging again (optional: only providers with recurring billing)."""
        raise NotConnected(f"{self.name} cannot cancel subscriptions yet")
