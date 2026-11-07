"""Tips and the payment pipeline: a signed message from the provider is the only thing that makes money real."""

import json
from unittest.mock import patch

import pytest

from app.core import ledger as books
from app.core.config import settings
from app.core.earnings import balance_paise
from app.core.ledger import CASH, EXPENSE_FEES, INCOME_TIP_FEE, OWED
from app.core.payments.providers import get_provider
from app.core.payments.providers.sandbox import SandboxProvider
from app.core.payments.types import PAYMENT_CAPTURED, PAYMENT_REFUNDED
from app.database import SessionLocal
from app.models.notification import Notification
from app.models.partner import EarningLine, EarningMonth, Payout
from app.models.payments import Payment, PaymentEvent
from tests.money_helpers import (
    WEBHOOK,
    balance,
    books_balanced,
    delivery,
    entries_for,
    fail,
    partner_writer,
    pay,
    payment,
    reader,
    refund,
    send,
    start_and_pay_tip,
    tip,
    wipe_programme_books,
)

ADMIN_EMAIL = "testuser@example.com"  # the address the mocked Google reply returns


@pytest.fixture(autouse=True)
def _sandbox_is_the_provider():
    with patch.object(settings, "PAYMENTS_PROVIDER", "sandbox"), patch("app.core.admin.settings.ADMIN_EMAILS", ADMIN_EMAIL):
        yield


@pytest.fixture(scope="module", autouse=True)
def _clean_slate():
    wipe_programme_books()
    yield
    wipe_programme_books()


@pytest.fixture(scope="module")
def admin(test_client):
    google = test_client.post("/api/auth/google-login", json={"token": "a" * 80})
    assert google.status_code == 200, google.text
    return {"headers": {"Authorization": f"Bearer {google.json()['access_token']}"}, "user": google.json()["user"]}


def the_writers_balance(user_id):
    with SessionLocal() as db:
        return balance_paise(db, user_id)


# --------------------------------------------------------------------------- the feature is off until a provider is connected

def test_without_a_provider_the_site_offers_nothing(test_client):
    who, other = partner_writer(test_client), reader(test_client)
    with patch.object(settings, "PAYMENTS_PROVIDER", ""):
        assert test_client.get("/api/payments/config").json() == {
            "enabled": False, "test_mode": False, "provider": None, "tips": None, "membership": None,
        }
        assert test_client.get(f"/api/payments/tips/target/{who['user']['id']}").json() == {"available": False}
        assert tip(test_client, other, who).status_code == 503
        assert test_client.post(WEBHOOK, content=b"{}").status_code == 404


def test_a_provider_that_is_only_a_skeleton_counts_as_no_provider(test_client):
    with patch.object(settings, "PAYMENTS_PROVIDER", "razorpay"):
        assert get_provider() is None
        assert test_client.get("/api/payments/config").json()["enabled"] is False
    with patch.object(settings, "PAYMENTS_PROVIDER", "no-such-provider"):
        assert get_provider() is None


def test_the_pretend_provider_is_refused_outside_development(test_client):
    from app.main import guard_payments_config

    with patch.object(settings, "debug", False):
        assert get_provider() is None
        with pytest.raises(RuntimeError):
            guard_payments_config()
    guard_payments_config()  # fine in development


def test_the_config_tells_the_page_what_it_may_show(test_client):
    config = test_client.get("/api/payments/config").json()
    assert config["enabled"] is True and config["test_mode"] is True and config["provider"] == "sandbox"
    assert config["tips"] == {"min_paise": 1000, "max_paise": 1_000_000, "presets_paise": [2000, 5000, 10000], "site_fee_bps": 1000}
    assert [p["code"] for p in config["membership"]["plans"]] == ["monthly", "yearly"]
    assert config["membership"]["plans"][0]["price_paise"] == 4900 and config["membership"]["gst_included"] is False


# --------------------------------------------------------------------------- who can be tipped

def test_only_a_partner_who_accepts_tips_can_be_tipped(test_client):
    payer, nobody = reader(test_client), reader(test_client)
    willing, unwilling = partner_writer(test_client), partner_writer(test_client, accepts_tips=False)

    assert test_client.get(f"/api/payments/tips/target/{willing['user']['id']}").json() == {"available": True}
    assert test_client.get(f"/api/payments/tips/target/{unwilling['user']['id']}").json() == {"available": False}
    assert test_client.get(f"/api/payments/tips/target/{nobody['user']['id']}").json() == {"available": False}
    assert tip(test_client, payer, unwilling).status_code == 404
    assert tip(test_client, payer, nobody).status_code == 404
    # A writer is never offered a tip button on their own page.
    assert test_client.get(f"/api/payments/tips/target/{willing['user']['id']}", headers=willing["headers"]).json() == {"available": False}
    assert tip(test_client, willing, willing).status_code == 400


def test_the_amount_is_held_to_the_limits_and_the_story_must_be_the_writers(test_client):
    payer, writer, other = reader(test_client), partner_writer(test_client), partner_writer(test_client)
    assert tip(test_client, payer, writer, "9.99").status_code == 400
    assert tip(test_client, payer, writer, "10001").status_code == 400
    assert tip(test_client, payer, writer, "0").status_code == 422
    assert tip(test_client, payer, writer, "10").status_code == 201
    assert tip(test_client, payer, writer, "10000").status_code == 201
    assert tip(test_client, payer, writer, post_id=writer["post_id"]).status_code == 201
    assert tip(test_client, payer, writer, post_id=other["post_id"]).status_code == 404  # someone else's story
    assert test_client.post("/api/payments/tips", json={"writer_id": writer["user"]["id"], "amount_rupees": "50"}).status_code == 401


def test_the_price_is_decided_by_the_server_not_the_browser(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    started = tip(test_client, payer, writer, "50.50").json()
    assert started["payment"]["amount_paise"] == 5050 and started["payment"]["status"] == "created"
    assert started["checkout"]["provider"] == "sandbox" and started["checkout"]["order_id"].startswith("sbx_order_")
    row = payment(started["payment"]["public_id"])
    assert row.purpose == "tip" and row.target_user_id == writer["user"]["id"] and row.user_id == payer["user"]["id"]


# --------------------------------------------------------------------------- a tip, from order to the writer's balance

def test_a_paid_tip_reaches_the_books_the_balance_and_the_bell(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    cash_before, fee_income_before, fee_cost_before = balance(CASH), balance(INCOME_TIP_FEE), balance(EXPENSE_FEES)

    started = tip(test_client, payer, writer, "50", message="बहुत सुंदर!", post_id=writer["post_id"]).json()
    public_id = started["payment"]["public_id"]
    assert the_writers_balance(writer["user"]["id"]) == 0  # an order is not money

    response = pay(test_client, public_id)
    assert response.json() == {"ok": True, "applied": 1, "ignored": 0, "rejected": 0, "errored": 0}

    row = payment(public_id)
    assert row.status == "captured" and row.writer_share_paise == 4500 and row.site_fee_paise == 500 and row.fee_paise == 118
    assert balance(OWED, writer["user"]["id"]) == 4500
    assert balance(INCOME_TIP_FEE) - fee_income_before == 500
    assert balance(EXPENSE_FEES) - fee_cost_before == 118
    assert balance(CASH) - cash_before == 5000 - 118
    assert entries_for(public_id)[:3] == [(CASH, 5000, None), (OWED, -4500, writer["user"]["id"]), (INCOME_TIP_FEE, -500, None)]
    assert books_balanced()

    mine = test_client.get("/api/partner/earnings", headers=writer["headers"]).json()
    assert mine["tips_paise"] == 4500 and mine["pool_paise"] == 0 and mine["earned_paise"] == 4500 and mine["balance_paise"] == 4500
    assert test_client.get(f"/api/payments/orders/{public_id}", headers=payer["headers"]).json()["status"] == "captured"

    bell = [n for n in test_client.get("/api/notifications", headers=writer["headers"]).json() if n["kind"] == "tip"]
    assert len(bell) == 1 and bell[0]["count"] == 4500 and bell[0]["text"] == "बहुत सुंदर!"
    assert bell[0]["actor"]["username"] == "लेखनी"  # the site speaks: the sender's account is not exposed


def test_the_writer_sees_who_tipped_unless_they_asked_not_to_be_named(test_client):
    writer, named, hidden = partner_writer(test_client), reader(test_client), reader(test_client)
    start_and_pay_tip(test_client, named, writer, "20", message="धन्यवाद")
    start_and_pay_tip(test_client, hidden, writer, "30", anonymous=True)

    received = test_client.get("/api/payments/tips/received", headers=writer["headers"]).json()
    assert received["count"] == 2 and received["total_paise"] == 1800 + 2700 and received["site_fee_bps"] == 1000
    by_amount = {item["amount_paise"]: item for item in received["items"]}
    assert by_amount[2000]["sender"]["username"] == named["user"]["username"] and by_amount[2000]["message"] == "धन्यवाद"
    assert by_amount[3000]["sender"] is None
    # Neither the bell nor anything the other reader can fetch carries the hidden sender's name.
    assert hidden["user"]["username"] not in json.dumps(test_client.get("/api/notifications", headers=writer["headers"]).json())
    # Only a partner has a tips page.
    assert test_client.get("/api/payments/tips/received", headers=named["headers"]).status_code == 403


def test_a_writer_can_switch_tips_off_and_on(test_client):
    writer, payer = partner_writer(test_client), reader(test_client)
    off = test_client.put("/api/payments/tips/settings", json={"accepts_tips": False}, headers=writer["headers"])
    assert off.json() == {"accepts_tips": False}
    assert tip(test_client, payer, writer).status_code == 404
    test_client.put("/api/payments/tips/settings", json={"accepts_tips": True}, headers=writer["headers"])
    assert tip(test_client, payer, writer).status_code == 201
    assert test_client.put("/api/payments/tips/settings", json={"accepts_tips": False}, headers=payer["headers"]).status_code == 403


def test_a_payment_is_private_to_the_person_who_made_it(test_client):
    payer, writer, snoop = reader(test_client), partner_writer(test_client), reader(test_client)
    public_id = tip(test_client, payer, writer).json()["payment"]["public_id"]
    assert test_client.get(f"/api/payments/orders/{public_id}", headers=payer["headers"]).status_code == 200
    assert test_client.get(f"/api/payments/orders/{public_id}", headers=snoop["headers"]).status_code == 404
    assert test_client.get(f"/api/payments/orders/{public_id}", headers=writer["headers"]).status_code == 404
    assert test_client.get(f"/api/payments/orders/{public_id}").status_code == 401


# --------------------------------------------------------------------------- the provider's messages are not simply believed

def test_a_message_without_the_providers_signature_changes_nothing(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = tip(test_client, payer, writer).json()["payment"]["public_id"]
    raw, headers = delivery(public_id, PAYMENT_CAPTURED)

    assert test_client.post(WEBHOOK, content=raw).status_code == 400  # no signature at all
    assert test_client.post(WEBHOOK, content=raw, headers={"x-sandbox-signature": "0" * 64}).status_code == 400
    # A real signature does not carry over to a doctored body (here: a bigger amount).
    doctored = raw.replace(b'"amount":5000', b'"amount":500000')
    assert doctored != raw
    assert test_client.post(WEBHOOK, content=doctored, headers=headers).status_code == 400
    assert payment(public_id).status == "created" and balance(OWED, writer["user"]["id"]) == 0
    # A genuinely signed body that is not a message at all is refused as malformed, not crashed on.
    garbage = b"not json"
    assert test_client.post(WEBHOOK, content=garbage, headers={"x-sandbox-signature": SandboxProvider.sign(garbage)}).status_code == 400


def test_a_repeated_delivery_counts_the_money_once(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = tip(test_client, payer, writer).json()["payment"]["public_id"]
    raw, headers = delivery(public_id, PAYMENT_CAPTURED, event_id="evt_repeat_1")
    first = test_client.post(WEBHOOK, content=raw, headers=headers).json()
    cash_after_first = balance(CASH)
    again = test_client.post(WEBHOOK, content=raw, headers=headers).json()

    assert first["applied"] == 1 and again["applied"] == 0 and again["ignored"] == 1
    assert balance(CASH) == cash_after_first and balance(OWED, writer["user"]["id"]) == 4500
    assert len([n for n in test_client.get("/api/notifications", headers=writer["headers"]).json() if n["kind"] == "tip"]) == 1


def test_a_second_message_for_a_payment_already_paid_is_ignored(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = start_and_pay_tip(test_client, payer, writer)
    again = pay(test_client, public_id).json()  # a fresh event id, same payment
    assert again["applied"] == 0 and again["ignored"] == 1
    assert balance(OWED, writer["user"]["id"]) == 4500


def test_money_that_does_not_match_the_order_is_refused_and_noted(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = tip(test_client, payer, writer, "50").json()["payment"]["public_id"]
    result = send(test_client, public_id, PAYMENT_CAPTURED, amount=100).json()  # ₹1 arrived for a ₹50 order
    assert result["rejected"] == 1 and result["applied"] == 0
    row = payment(public_id)
    assert row.status == "created" and row.failure_reason and balance(OWED, writer["user"]["id"]) == 0
    with SessionLocal() as db:
        event = db.query(PaymentEvent).order_by(PaymentEvent.id.desc()).first()
        assert event.status == "rejected" and "100" in event.detail

    assert send(test_client, public_id, PAYMENT_CAPTURED, currency="USD").json()["rejected"] == 1
    # The genuine amount, afterwards, still works.
    assert pay(test_client, public_id).json()["applied"] == 1


def test_a_failed_attempt_can_be_followed_by_a_good_one_but_not_the_other_way(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    first = tip(test_client, payer, writer).json()["payment"]["public_id"]
    assert fail(test_client, first, reason="card declined").json()["applied"] == 1
    assert payment(first).status == "failed"
    assert pay(test_client, first).json()["applied"] == 1
    assert payment(first).status == "captured"

    # A late "failed" about an attempt that was superseded must not undo a paid payment.
    assert fail(test_client, first).json()["ignored"] == 1
    assert payment(first).status == "captured" and balance(OWED, writer["user"]["id"]) == 4500


def test_a_message_about_a_payment_we_never_made_is_ignored_not_an_error(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = tip(test_client, payer, writer).json()["payment"]["public_id"]
    raw, headers = delivery(public_id, PAYMENT_CAPTURED, reference="f" * 32, order_id="sbx_order_unknown", payment_id="sbx_pay_unknown")
    result = test_client.post(WEBHOOK, content=raw, headers=headers)
    assert result.status_code == 200 and result.json()["ignored"] == 1
    assert books_balanced()


def test_only_the_connected_providers_webhook_exists(test_client):
    assert test_client.post("/api/payments/webhooks/razorpay", content=b"{}").status_code == 404
    assert test_client.post("/api/payments/webhooks/anything", content=b"{}").status_code == 404
    assert test_client.post(WEBHOOK, content=b"x" * (70 * 1024)).status_code == 413


# --------------------------------------------------------------------------- refunds

def test_a_partial_then_a_full_refund_take_back_exactly_what_was_given(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = start_and_pay_tip(test_client, payer, writer, "50")
    uid = writer["user"]["id"]
    cash_before = balance(CASH)

    assert refund(test_client, public_id, 2000).json()["applied"] == 1
    row = payment(public_id)
    assert row.status == "part_refunded" and row.refunded_paise == 2000
    assert balance(OWED, uid) == 4500 - 1800  # 20/50 of the writer's share
    assert balance(CASH) == cash_before - 2000
    assert the_writers_balance(uid) == 2700

    assert refund(test_client, public_id, 3000).json()["applied"] == 1
    row = payment(public_id)
    assert row.status == "refunded" and row.refunded_paise == 5000
    assert balance(OWED, uid) == 0 and the_writers_balance(uid) == 0
    # Nothing more can be refunded than was paid.
    assert refund(test_client, public_id, 100).json()["ignored"] == 1
    assert books_balanced()


def test_a_refund_message_repeated_is_not_refunded_twice(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = start_and_pay_tip(test_client, payer, writer, "50")
    raw, headers = delivery(public_id, PAYMENT_REFUNDED, event_id="evt_refund_dup", refund_id="r_dup", refund_amount=1000)
    test_client.post(WEBHOOK, content=raw, headers=headers)
    test_client.post(WEBHOOK, content=raw, headers=headers)
    assert payment(public_id).refunded_paise == 1000


def test_a_refund_that_arrives_before_the_payment_is_asked_to_come_again(test_client):
    payer, writer = reader(test_client), partner_writer(test_client)
    public_id = tip(test_client, payer, writer, "50").json()["payment"]["public_id"]
    raw, headers = delivery(public_id, PAYMENT_REFUNDED, event_id="evt_early_refund", refund_id="r_early", refund_amount=5000)

    early = test_client.post(WEBHOOK, content=raw, headers=headers)
    assert early.status_code == 500 and early.json()["errored"] == 1  # the provider will deliver it again
    with SessionLocal() as db:
        assert db.query(PaymentEvent).filter(PaymentEvent.event_id == "evt_early_refund").one().status == "errored"

    pay(test_client, public_id)
    retry = test_client.post(WEBHOOK, content=raw, headers=headers)
    assert retry.status_code == 200 and retry.json()["applied"] == 1
    assert payment(public_id).status == "refunded" and balance(OWED, writer["user"]["id"]) == 0


# --------------------------------------------------------------------------- paying the writer

def test_a_payout_can_use_tip_money_and_never_more_than_is_owed(test_client, admin):
    payer, writer = reader(test_client), partner_writer(test_client)
    uid = writer["user"]["id"]
    start_and_pay_tip(test_client, payer, writer, "100")  # the writer's share: ₹90
    cash_before = balance(CASH)

    too_much = test_client.post("/api/earnings/payouts", json={"user_id": uid, "amount_rupees": "90.01"}, headers=admin["headers"])
    assert too_much.status_code == 400

    paid = test_client.post("/api/earnings/payouts", json={"user_id": uid, "amount_rupees": "40", "reference": "UPI123"}, headers=admin["headers"])
    assert paid.status_code == 201
    body = paid.json()
    assert body["balance_paise"] == 5000 and body["tips_paise"] == 9000 and body["earned_paise"] == 9000 and body["paid_paise"] == 4000
    assert balance(OWED, uid) == 5000 and balance(CASH) == cash_before - 4000
    mine = test_client.get("/api/partner/earnings", headers=writer["headers"]).json()
    assert mine["balance_paise"] == 5000 and mine["payouts"][0]["amount_paise"] == 4000
    assert books_balanced()


def test_the_books_agree_with_the_balance_a_writer_is_shown(test_client):
    """Pool earnings, tips and payouts all meet in one balance, and the books say the same number."""
    from app.core.clock import utcnow
    from app.core.payments import accounting

    payer, writer = reader(test_client), partner_writer(test_client)
    uid = writer["user"]["id"]
    # A month finalized and a payout made before the books existed: the books catch up on their own.
    with SessionLocal() as db:
        month = EarningMonth(month="2025-07", pool_paise=100_000, finalized_at=utcnow(), total_points=3, distributed_paise=30_000)
        db.add(month)
        db.flush()
        db.add(EarningLine(month_id=month.id, user_id=uid, points=3, amount_paise=30_000))
        db.add(Payout(user_id=uid, amount_paise=10_000, reference="legacy", paid_at=utcnow()))
        db.commit()
    start_and_pay_tip(test_client, payer, writer, "100")  # +₹90

    try:
        with SessionLocal() as db:
            accounting.sync_programme(db)
            accounting.sync_programme(db)  # a second catch-up changes nothing
            expected = 30_000 + 9_000 - 10_000
            assert balance_paise(db, uid) == expected == books.balance(db, books.OWED, user_id=uid)
            assert books.is_balanced(db)
    finally:
        wipe_programme_books()
        with SessionLocal() as db:
            db.query(Payout).filter(Payout.reference == "legacy").delete()
            db.query(EarningLine).filter(EarningLine.user_id == uid).delete()
            db.query(EarningMonth).filter(EarningMonth.month == "2025-07").delete()
            db.commit()


# --------------------------------------------------------------------------- the pretend provider's screen

def test_the_sandbox_buttons_run_the_same_path_as_a_real_provider(test_client):
    payer, writer, other = reader(test_client), partner_writer(test_client), reader(test_client)
    public_id = tip(test_client, payer, writer).json()["payment"]["public_id"]

    assert test_client.post(f"/api/payments/sandbox/{public_id}/pay", headers=other["headers"]).status_code == 404  # not their payment
    assert test_client.post(f"/api/payments/sandbox/{public_id}/nonsense", headers=payer["headers"]).status_code == 404
    assert test_client.post(f"/api/payments/sandbox/{public_id}/fail", headers=payer["headers"]).json()["status"] == "failed"
    assert test_client.post(f"/api/payments/sandbox/{public_id}/pay", headers=payer["headers"]).json()["status"] == "captured"
    assert test_client.post(f"/api/payments/sandbox/{public_id}/refund", headers=payer["headers"]).json()["status"] == "refunded"
    assert balance(OWED, writer["user"]["id"]) == 0

    with patch.object(settings, "PAYMENTS_PROVIDER", ""):
        assert test_client.post(f"/api/payments/sandbox/{public_id}/pay", headers=payer["headers"]).status_code == 404
    with patch.object(settings, "debug", False):
        assert test_client.post(f"/api/payments/sandbox/{public_id}/pay", headers=payer["headers"]).status_code == 404
