"""An offline pretend provider, for development and tests.

It is a complete, working provider: it hands out fake orders, and its "webhooks" are real signed
messages that go through exactly the same verify, parse and apply path a real provider's would.
That is the point of it: the whole payment flow (tip, membership, refund, repeat deliveries) can be
exercised end to end without any account, network or money.

It exists only while DEBUG is on. The registry refuses it otherwise, and so does application start-up.
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Mapping, Optional, Tuple

from app.core.config import settings
from app.core.payments.types import PAYMENT_CAPTURED, BadWebhook, Checkout, CheckoutRequest, ProviderEvent
from app.core.payments.providers.base import PaymentProvider

SIGNATURE_HEADER = "x-sandbox-signature"


def _fee(amount_paise: int) -> int:
    """About what a real gateway keeps: 2% plus 18% GST on it."""
    return (amount_paise * 236 + 5000) // 10000


class SandboxProvider(PaymentProvider):
    name = "sandbox"

    def configured(self) -> bool:
        return bool(settings.debug)

    # ------------------------------------------------------------------ checkout
    def create_checkout(self, request: CheckoutRequest) -> Checkout:
        return Checkout(
            provider=self.name,
            order_id=f"sbx_order_{request.reference}",
            subscription_id=f"sbx_sub_{request.reference}" if request.recurring else None,
            client={"mode": "sandbox", "reference": request.reference},
        )

    def cancel_subscription(self, subscription_id: str) -> None:
        return None  # nothing is charged offline, so there is nothing to stop

    # ------------------------------------------------------------------ webhooks
    @staticmethod
    def sign(body: bytes) -> str:
        return hmac.new(settings.PAYMENTS_SANDBOX_SECRET.encode(), body, hashlib.sha256).hexdigest()

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        return hmac.compare_digest(self.sign(body), headers.get(SIGNATURE_HEADER, ""))

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> List[ProviderEvent]:
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            when = data.get("created_at")
            return [
                ProviderEvent(
                    id=str(data["id"]),
                    type=str(data["type"]),
                    reference=data.get("reference"),
                    order_id=data.get("order_id"),
                    payment_id=data.get("payment_id"),
                    subscription_id=data.get("subscription_id"),
                    amount_paise=_int(data.get("amount")),
                    currency=data.get("currency"),
                    fee_paise=_int(data.get("fee")),
                    refund_id=data.get("refund_id"),
                    refund_paise=_int(data.get("refund_amount")),
                    reason=data.get("reason"),
                    occurred_at=datetime.fromtimestamp(float(when), tz=timezone.utc).replace(tzinfo=None) if when else None,
                    log={k: data.get(k) for k in ("type", "amount", "currency", "fee", "refund_amount", "reason")},
                )
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise BadWebhook(str(exc)) from exc

    # ------------------------------------------------------------------ the pretend provider's own side
    @classmethod
    def delivery(cls, kind: str, payment, **extra) -> Tuple[bytes, Dict[str, str]]:
        """A signed delivery for ``payment`` ready to hand to the webhook handler (what the sandbox
        screen does when the person presses "pay", "fail" or "refund")."""
        body = {
            "id": extra.pop("event_id", f"sbx_evt_{uuid.uuid4().hex[:16]}"),
            "type": kind,
            "created_at": time.time(),
            "reference": payment.public_id,
            "order_id": payment.provider_order_id,
            "payment_id": payment.provider_payment_id or f"sbx_pay_{payment.public_id}",
            "subscription_id": extra.pop("subscription_id", payment.provider_subscription_id),
            "amount": int(payment.amount_paise),
            "currency": payment.currency,
        }
        if kind == PAYMENT_CAPTURED:
            body["fee"] = _fee(int(payment.amount_paise))
        body.update(extra)
        raw = json.dumps(body, separators=(",", ":")).encode()
        return raw, {SIGNATURE_HEADER: cls.sign(raw), "content-type": "application/json"}


def _int(value) -> Optional[int]:
    return None if value is None else int(value)

