"""Membership: paid time starts only when the provider confirms the money, and "was a member then" can be answered."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.core import membership as members
from app.core.clock import utcnow
from app.core.config import settings
from app.core.ledger import CASH, GST, INCOME_MEMBERSHIP
from app.core.payments.providers.sandbox import SandboxProvider
from app.core.payments.types import SUBSCRIPTION_CANCELLED
from app.database import SessionLocal
from app.models.payments import Membership, MembershipPeriod, Payment
from tests.money_helpers import balance, books_balanced, pay, payment, reader, refund, send


@pytest.fixture(autouse=True)
def _sandbox_is_the_provider():
    with patch.object(settings, "PAYMENTS_PROVIDER", "sandbox"):
        yield


def checkout(client, who, plan="monthly", auto_renew=True):
    return client.post("/api/membership/checkout", json={"plan": plan, "auto_renew": auto_renew}, headers=who["headers"])


def me(client, who):
    response = client.get("/api/membership/me", headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def join(client, who, plan="monthly", auto_renew=True) -> str:
    response = checkout(client, who, plan, auto_renew)
    assert response.status_code == 201, response.text
    public_id = response.json()["payment"]["public_id"]
    pay(client, public_id)
    return public_id


def public_view(client, who):
    return client.get(f"/api/users/{who['user']['id']}").json()


# --------------------------------------------------------------------------- buying

def test_a_membership_begins_only_when_the_money_arrives(test_client):
    who = reader(test_client)
    assert me(test_client, who)["is_member"] is False

    started = checkout(test_client, who)
    assert started.status_code == 201 and started.json()["payment"]["amount_paise"] == 4900
    assert started.json()["checkout"]["subscription_id"].startswith("sbx_sub_")  # a self-renewing one
    assert me(test_client, who)["is_member"] is False  # an order is not a membership
    assert public_view(test_client, who)["is_member"] is False

    income, cash = balance(INCOME_MEMBERSHIP), balance(CASH)
    pay(test_client, started.json()["payment"]["public_id"])
    state = me(test_client, who)
    assert state["is_member"] is True and state["auto_renew"] is True and state["plan_code"] == "monthly"
    paid_until = datetime.fromisoformat(state["paid_until"])
    assert timedelta(days=27) < paid_until - utcnow() < timedelta(days=32)
    assert balance(INCOME_MEMBERSHIP) - income == 4900 and balance(CASH) - cash == 4900 - 116  # the sandbox fee on ₹49
    assert public_view(test_client, who)["is_member"] is True
    assert books_balanced()


def test_the_price_comes_from_the_plan_never_from_the_request(test_client):
    who = reader(test_client)
    assert checkout(test_client, who, "platinum").status_code == 404
    body = test_client.post("/api/membership/checkout", json={"plan": "yearly", "auto_renew": False, "amount_rupees": "1"}, headers=who["headers"])
    assert body.status_code == 201 and body.json()["payment"]["amount_paise"] == 39900
    assert body.json()["checkout"]["subscription_id"] is None  # not renewing
    assert test_client.post("/api/membership/checkout", json={"plan": "monthly"}).status_code == 401
    with patch.object(settings, "PAYMENTS_PROVIDER", ""):
        assert checkout(test_client, who).status_code == 503


def test_the_gst_inside_the_price_is_kept_apart_from_income(test_client):
    who = reader(test_client)
    income, tax, cash = balance(INCOME_MEMBERSHIP), balance(GST), balance(CASH)
    with patch.object(settings, "GST_PERCENT", 18):
        join(test_client, who)
    assert balance(INCOME_MEMBERSHIP) - income == 4153 and balance(GST) - tax == 747
    assert balance(CASH) - cash == 4900 - 116
    assert books_balanced()


def test_time_paid_for_follows_on_from_time_already_left(test_client):
    who = reader(test_client)
    join(test_client, who, "monthly", auto_renew=False)
    first_end = datetime.fromisoformat(me(test_client, who)["paid_until"])
    join(test_client, who, "yearly", auto_renew=False)
    second_end = datetime.fromisoformat(me(test_client, who)["paid_until"])
    assert second_end == members.add_months(first_end, 12)  # starts where the first ended, not today
    with SessionLocal() as db:
        assert db.query(MembershipPeriod).filter(MembershipPeriod.user_id == who["user"]["id"]).count() == 2


def test_the_same_payment_cannot_buy_time_twice(test_client):
    who = reader(test_client)
    public_id = join(test_client, who)
    before = me(test_client, who)["paid_until"]
    pay(test_client, public_id)
    assert me(test_client, who)["paid_until"] == before


# --------------------------------------------------------------------------- the reader's choices

def test_the_badge_is_shown_only_if_the_member_wants_it(test_client):
    who = reader(test_client)
    assert test_client.put("/api/membership/badge", json={"show": False}, headers=who["headers"]).status_code == 400  # no membership yet
    join(test_client, who)
    assert public_view(test_client, who)["is_member"] is True

    hidden = test_client.put("/api/membership/badge", json={"show": False}, headers=who["headers"]).json()
    assert hidden["show_badge"] is False and hidden["is_member"] is True  # still a member, just not on show
    assert public_view(test_client, who)["is_member"] is False
    test_client.put("/api/membership/badge", json={"show": True}, headers=who["headers"])
    assert public_view(test_client, who)["is_member"] is True


def test_cancelling_stops_renewal_but_keeps_the_time_already_paid(test_client):
    who = reader(test_client)
    assert test_client.post("/api/membership/cancel", headers=who["headers"]).status_code == 400  # nothing to cancel
    join(test_client, who)
    ended = test_client.post("/api/membership/cancel", headers=who["headers"]).json()
    assert ended["is_member"] is True and ended["auto_renew"] is False and ended["cancelled"] is True


def test_a_membership_is_not_called_cancelled_if_the_provider_would_still_charge(test_client):
    who = reader(test_client)
    join(test_client, who)
    with patch.object(SandboxProvider, "cancel_subscription", side_effect=RuntimeError("provider down")):
        refused = test_client.post("/api/membership/cancel", headers=who["headers"])
    assert refused.status_code == 503
    assert me(test_client, who)["auto_renew"] is True and me(test_client, who)["cancelled"] is False


def test_the_provider_cancelling_a_subscription_is_heard(test_client):
    who = reader(test_client)
    public_id = join(test_client, who)
    assert send(test_client, public_id, SUBSCRIPTION_CANCELLED).json()["applied"] == 1
    state = me(test_client, who)
    assert state["auto_renew"] is False and state["is_member"] is True
    assert send(test_client, public_id, SUBSCRIPTION_CANCELLED, subscription_id="sbx_sub_nobody").json()["ignored"] == 1


# --------------------------------------------------------------------------- renewing and refunds

def test_a_renewal_charge_adds_the_next_period_at_the_plans_price(test_client):
    who = reader(test_client)
    public_id = join(test_client, who)
    first_end = datetime.fromisoformat(me(test_client, who)["paid_until"])
    income = balance(INCOME_MEMBERSHIP)

    renewed = test_client.post(f"/api/payments/sandbox/{public_id}/renew", headers=who["headers"])
    assert renewed.status_code == 200
    assert datetime.fromisoformat(me(test_client, who)["paid_until"]) == members.add_months(first_end, 1)
    assert balance(INCOME_MEMBERSHIP) - income == 4900
    with SessionLocal() as db:
        assert db.query(Payment).filter(Payment.user_id == who["user"]["id"], Payment.purpose == "membership").count() == 2

    # A renewal for a subscription we do not know of is not turned into a membership out of thin air.
    stranger = reader(test_client)
    other = join(test_client, stranger)
    with SessionLocal() as db:
        db.query(Membership).filter(Membership.user_id == stranger["user"]["id"]).update({"provider_subscription_id": "someone-elses"})
        db.commit()
    assert test_client.post(f"/api/payments/sandbox/{other}/renew", headers=stranger["headers"]).status_code == 200
    assert datetime.fromisoformat(me(test_client, stranger)["paid_until"]) - utcnow() < timedelta(days=32)  # no second month


def test_a_full_refund_takes_the_membership_back_and_a_part_does_not(test_client):
    who = reader(test_client)
    public_id = join(test_client, who)
    income = balance(INCOME_MEMBERSHIP)

    assert refund(test_client, public_id, 1000).json()["applied"] == 1
    assert me(test_client, who)["is_member"] is True
    assert income - balance(INCOME_MEMBERSHIP) == 1000

    assert refund(test_client, public_id, 3900).json()["applied"] == 1
    state = me(test_client, who)
    assert state["is_member"] is False and state["ever_member"] is False
    assert public_view(test_client, who)["is_member"] is False
    assert income - balance(INCOME_MEMBERSHIP) == 4900
    assert books_balanced()


# --------------------------------------------------------------------------- "was a member then"

def test_calendar_months_are_added_the_way_people_count_them():
    assert members.add_months(datetime(2026, 1, 31, 9, 30), 1) == datetime(2026, 2, 28, 9, 30)
    assert members.add_months(datetime(2024, 1, 31), 1) == datetime(2024, 2, 29)  # a leap year
    assert members.add_months(datetime(2026, 11, 15), 3) == datetime(2027, 2, 15)
    assert members.add_months(datetime(2026, 3, 10), 12) == datetime(2027, 3, 10)


def test_whether_someone_was_a_member_at_a_moment_is_answered_from_their_periods(test_client):
    who = reader(test_client)
    uid = who["user"]["id"]
    start, end = datetime(2025, 3, 10), datetime(2025, 4, 10)
    with SessionLocal() as db:
        paid = Payment(public_id="p" * 32, user_id=uid, purpose="membership", status="captured", amount_paise=4900,
                       provider="sandbox", plan_code="monthly")
        db.add(paid)
        db.flush()
        db.add(MembershipPeriod(user_id=uid, payment_id=paid.id, starts_at=start, ends_at=end))
        voided = Payment(public_id="q" * 32, user_id=uid, purpose="membership", status="refunded", amount_paise=4900,
                         provider="sandbox", plan_code="monthly")
        db.add(voided)
        db.flush()
        db.add(MembershipPeriod(user_id=uid, payment_id=voided.id, starts_at=datetime(2025, 6, 1), ends_at=datetime(2025, 7, 1), voided_at=utcnow()))
        db.commit()

        was_member = members.membership_lookup(db, datetime(2025, 1, 1), datetime(2025, 12, 31))
        assert was_member(uid, start) is True                       # the first instant counts
        assert was_member(uid, datetime(2025, 3, 25)) is True
        assert was_member(uid, end) is False                        # the last does not
        assert was_member(uid, datetime(2025, 3, 9, 23, 59)) is False
        assert was_member(uid, datetime(2025, 6, 15)) is False      # a refunded period never counted
        assert was_member(uid + 10**6, start) is False
        assert members.is_member(db, uid, datetime(2025, 3, 25)) is True
        assert members.member_ids(db, [uid, uid + 1], datetime(2025, 3, 25)) == {uid}
        assert members.member_ids(db, [], datetime(2025, 3, 25)) == set()


# --------------------------------------------------------------------------- a member must never be charged without getting time

def test_a_renewal_is_honoured_at_the_price_the_subscription_was_started_at(test_client):
    """The owner changes the price list; subscriptions already running keep charging what they started at."""
    who = reader(test_client)
    public_id = join(test_client, who)                       # started at ₹49
    first_end = datetime.fromisoformat(me(test_client, who)["paid_until"])
    income = balance(INCOME_MEMBERSHIP)

    with patch.object(settings, "MEMBERSHIP_MONTHLY_RUPEES", 59):
        renewed = test_client.post(f"/api/payments/sandbox/{public_id}/renew", headers=who["headers"])
    assert renewed.status_code == 200
    assert datetime.fromisoformat(me(test_client, who)["paid_until"]) == members.add_months(first_end, 1)  # time was given
    assert balance(INCOME_MEMBERSHIP) - income == 4900                                                       # for ₹49, as charged
    with SessionLocal() as db:
        renewal = db.query(Payment).filter(Payment.user_id == who["user"]["id"]).order_by(Payment.id.desc()).first()
        assert renewal.status == "captured" and renewal.amount_paise == 4900
    # ... while someone starting NOW pays the new price.
    with patch.object(settings, "MEMBERSHIP_MONTHLY_RUPEES", 59):
        assert checkout(test_client, reader(test_client)).json()["payment"]["amount_paise"] == 5900


def test_a_charge_that_matches_nothing_we_asked_for_is_still_refused(test_client):
    who = reader(test_client)
    public_id = join(test_client, who)
    income = balance(INCOME_MEMBERSHIP)
    raw, headers = SandboxProvider.delivery(
        "payment.captured", payment(public_id), reference=None, order_id=None, payment_id="sbx_odd_amount",
        subscription_id=payment(public_id).provider_subscription_id, amount=12300,
    )
    result = test_client.post("/api/payments/webhooks/sandbox", content=raw, headers=headers).json()
    assert result["rejected"] == 1 and balance(INCOME_MEMBERSHIP) == income


def test_a_member_who_already_renews_cannot_start_a_second_subscription(test_client):
    who = reader(test_client)
    join(test_client, who)                                    # renews on its own
    refused = checkout(test_client, who, "yearly", auto_renew=True)
    assert refused.status_code == 400 and "पहले से अपने-आप" in refused.json()["detail"]
    # Extra time without renewing is fine, and after cancelling renewal a new subscription can start again.
    assert checkout(test_client, who, "yearly", auto_renew=False).status_code == 201
    test_client.post("/api/membership/cancel", headers=who["headers"])
    assert checkout(test_client, who, "yearly", auto_renew=True).status_code == 201
