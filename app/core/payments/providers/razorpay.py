"""SKELETON: the Razorpay adapter. Nothing here talks to Razorpay yet.

The owner has not chosen a provider. This file only shows where one plugs in and what is left to
write; until it is filled in, ``configured()`` is False, so even PAYMENTS_PROVIDER=razorpay leaves the
site with no payment features (nothing half-works). To connect it:

  1. Create the account, finish business verification, and put the keys in the environment:
     PAYMENTS_API_KEY, PAYMENTS_API_SECRET, PAYMENTS_WEBHOOK_SECRET (the secret is chosen in the
     provider's dashboard when the webhook is added).
  2. In the dashboard, add a webhook pointing at  <backend>/api/payments/webhooks/razorpay  for the
     events that map to ours: payment captured, payment failed, refund processed and, for
     self-renewing memberships, subscription charged and cancelled.
  3. Write the four methods below. The shapes they take and return are in app/core/payments/types.py;
     tests/test_payments.py runs the sandbox through the whole flow and is the model for testing this one
     (sign a sample body with the webhook secret, post it, check the books).
  4. Flip ``configured()`` to check the three credentials, set PAYMENTS_PROVIDER=razorpay, redeploy.

Things to settle with the provider's own documentation before writing the code (not guessed here): the
exact signature scheme and header for webhooks, where the order's receipt/notes carry our reference,
which fields hold the fee and the refund, how a subscription is created, and the amount's unit.
"""

from typing import List, Mapping

from app.core.payments.providers.base import PaymentProvider
from app.core.payments.types import Checkout, CheckoutRequest, NotConnected, ProviderEvent


class RazorpayProvider(PaymentProvider):
    name = "razorpay"

    def configured(self) -> bool:
        return False  # skeleton: becomes "all three credentials are present" once the methods below exist

    def create_checkout(self, request: CheckoutRequest) -> Checkout:
        raise NotConnected("razorpay: create_checkout is not written yet")

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        raise NotConnected("razorpay: verify_webhook is not written yet")

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> List[ProviderEvent]:
        raise NotConnected("razorpay: parse_webhook is not written yet")
