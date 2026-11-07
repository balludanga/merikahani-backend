"""The payment providers the site knows about, and the one that is switched on.

PAYMENTS_PROVIDER names one of them. Empty (the default) means the site takes no money and shows
nothing about it. To add a provider: write an adapter beside ``razorpay.py`` (it is a skeleton that
lists the steps), add it to ``PROVIDERS`` below, and set PAYMENTS_PROVIDER to its name.
"""

import logging
from typing import Dict, Optional

from app.core.config import settings
from app.core.payments.providers.base import PaymentProvider
from app.core.payments.providers.razorpay import RazorpayProvider
from app.core.payments.providers.sandbox import SandboxProvider

logger = logging.getLogger(__name__)

PROVIDERS: Dict[str, PaymentProvider] = {p.name: p for p in (SandboxProvider(), RazorpayProvider())}


def provider_named(name: str) -> Optional[PaymentProvider]:
    """A connected provider by name, or None (unknown, not connected, or the sandbox outside development)."""
    provider = PROVIDERS.get((name or "").strip().lower())
    if provider is None or not provider.configured():
        return None
    return provider


def get_provider() -> Optional[PaymentProvider]:
    """The provider the site uses, or None: then the site takes no payments."""
    wanted = settings.PAYMENTS_PROVIDER
    if not wanted:
        return None
    provider = provider_named(wanted)
    if provider is None:
        logger.warning("PAYMENTS_PROVIDER=%s is not usable (unknown, not connected, or the sandbox outside DEBUG)", wanted)
    return provider


def payments_enabled() -> bool:
    return get_provider() is not None
