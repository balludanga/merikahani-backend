"""Shared set-up for the money tests: the pretend provider, partners and readers, signed deliveries, and a look at the books."""

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core import ledger as books
from app.core.clock import utcnow
from app.core.payments.providers.sandbox import SandboxProvider
from app.core.payments.types import PAYMENT_CAPTURED, PAYMENT_FAILED, PAYMENT_REFUNDED
from app.database import SessionLocal
from app.models.ledger import LedgerEntry, LedgerTransaction
from app.models.partner import APPROVED, PartnerApplication
from app.models.payments import Payment
from tests.conftest import _register_and_login
from tests.test_posts import _create_post

WEBHOOK = "/api/payments/webhooks/sandbox"


def epoch(moment: datetime) -> float:
    """A naive-UTC moment as the epoch seconds a delivery carries."""
    return moment.replace(tzinfo=timezone.utc).timestamp()


def wipe_programme_books() -> None:
    """The pool and payout postings are keyed by row id, and the test database reuses ids after a wipe: clear
    those postings along with the rows they describe."""
    db = SessionLocal()
    try:
        ids = [i for (i,) in db.query(LedgerTransaction.id).filter(LedgerTransaction.kind.in_(("pool", "payout")))]
        if ids:
            db.query(LedgerEntry).filter(LedgerEntry.txn_id.in_(ids)).delete(synchronize_session=False)
            db.query(LedgerTransaction).filter(LedgerTransaction.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()


def partner_writer(client, accepts_tips: bool = True) -> dict:
    """A signed-in writer with a public story, already approved as a partner."""
    who = _register_and_login(client)
    who["post_id"] = _create_post(client, who["headers"], f"समर्थन की रचना {uuid.uuid4().hex[:6]}")["id"]
    db = SessionLocal()
    try:
        db.add(
            PartnerApplication(
                user_id=who["user"]["id"],
                status=APPROVED,
                applied_at=utcnow() - timedelta(days=40),
                approved_at=utcnow() - timedelta(days=30),
                accepts_tips=accepts_tips,
            )
        )
        db.commit()
    finally:
        db.close()
    return who


def reader(client) -> dict:
    return _register_and_login(client)


def payment(public_id: str) -> Payment:
    db = SessionLocal()
    try:
        row = db.query(Payment).filter(Payment.public_id == public_id).one()
        db.expunge(row)
        return row
    finally:
        db.close()


def delivery(public_id: str, kind: str, **extra):
    """(raw body, headers) for a signed message about the payment, exactly as the sandbox sends one."""
    return SandboxProvider.delivery(kind, payment(public_id), **extra)


def send(client, public_id: str, kind: str, **extra):
    raw, headers = delivery(public_id, kind, **extra)
    return client.post(WEBHOOK, content=raw, headers=headers)


def pay(client, public_id: str, **extra):
    response = send(client, public_id, PAYMENT_CAPTURED, **extra)
    assert response.status_code == 200, response.text
    return response


def fail(client, public_id: str, **extra):
    return send(client, public_id, PAYMENT_FAILED, **extra)


def refund(client, public_id: str, amount: int, refund_id: Optional[str] = None, **extra):
    return send(client, public_id, PAYMENT_REFUNDED, refund_id=refund_id or f"r_{uuid.uuid4().hex[:8]}", refund_amount=amount, **extra)


def tip(client, payer: dict, writer: dict, rupees="50", **body):
    payload = {"writer_id": writer["user"]["id"], "amount_rupees": rupees, **body}
    return client.post("/api/payments/tips", json=payload, headers=payer["headers"])


def start_and_pay_tip(client, payer: dict, writer: dict, rupees="50", **body) -> str:
    response = tip(client, payer, writer, rupees, **body)
    assert response.status_code == 201, response.text
    public_id = response.json()["payment"]["public_id"]
    pay(client, public_id)
    return public_id


# ------------------------------------------------------------------ a look at the books

def balance(account: str, user_id: Optional[int] = None) -> int:
    db = SessionLocal()
    try:
        return books.balance(db, account, user_id=user_id)
    finally:
        db.close()


def books_balanced() -> bool:
    db = SessionLocal()
    try:
        return books.is_balanced(db)
    finally:
        db.close()


def entries_for(public_id: str):
    """Every (account, amount, user) written for one payment, in the order written."""
    db = SessionLocal()
    try:
        return [
            (e.account, e.amount_paise, e.user_id)
            for e in db.query(LedgerEntry)
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.txn_id)
            .join(Payment, Payment.id == LedgerTransaction.payment_id)
            .filter(Payment.public_id == public_id)
            .order_by(LedgerEntry.id)
        ]
    finally:
        db.close()
