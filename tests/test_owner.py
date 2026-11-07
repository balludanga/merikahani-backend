"""The owner's money pages: who can see them, what the monthly sheet works out, and the books behind it."""

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.ledger import CASH
from app.core.payments.types import PAYMENT_CAPTURED
from app.database import SessionLocal
from app.models.ledger import ProfitSheet
from app.models.partner import EarningLine, EarningMonth, PartnerApplication, Payout
from app.models.user import User
from tests.conftest import _register_and_login
from tests.money_helpers import (
    balance,
    epoch,
    partner_writer,
    pay,
    reader,
    send,
    tip,
    wipe_programme_books,
)


def verified_account(client):
    """A signed-in account whose address Google has vouched for (the flag is set directly: no real Google here)."""
    who = _register_and_login(client)
    with SessionLocal() as db:
        db.query(User).filter(User.id == who["user"]["id"]).update({"email_verified": True})
        db.commit()
    return who


@pytest.fixture(scope="module")
def owner(test_client):
    return verified_account(test_client)


@pytest.fixture(scope="module")
def reviewer(test_client):
    return verified_account(test_client)


@pytest.fixture(autouse=True)
def _roles(owner, reviewer):
    with patch.object(settings, "PAYMENTS_PROVIDER", "sandbox"), \
            patch("app.core.admin.settings.OWNER_EMAILS", owner["user"]["email"]), \
            patch("app.core.admin.settings.ADMIN_EMAILS", reviewer["user"]["email"]):
        yield


def _wipe():
    wipe_programme_books()
    with SessionLocal() as db:
        for model in (Payout, EarningLine, EarningMonth, PartnerApplication, ProfitSheet):
            db.query(model).delete()
        db.commit()


@pytest.fixture(scope="module", autouse=True)
def _clean_slate():
    _wipe()
    _september_is_seeded.clear()
    yield
    _wipe()


def go(client, who, method, path, **kwargs):
    headers = who["headers"] if who else {}
    return getattr(client, method)(f"/api/owner{path}", headers=headers, **kwargs)


def add_entry(client, owner, account, rupees, day, memo=None):
    body = {"account": account, "amount_rupees": rupees, "on": day}
    if memo:
        body["memo"] = memo
    response = go(client, owner, "post", "/books/entries", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def sheet(client, owner, month):
    response = go(client, owner, "get", f"/profit/{month}")
    assert response.status_code == 200, response.text
    return response.json()


OWNER_ONLY = [
    ("get", "/profit/2025-09", None),
    ("put", "/profit/2025-09", {"pool_percent": 10, "holdback_percent": 0}),
    ("post", "/profit/2025-09/apply", None),
    ("get", "/books", None),
    ("post", "/books/entries", {"account": "income_other", "amount_rupees": "1"}),
    ("post", "/books/1/reverse", None),
    ("get", "/payments", None),
]


# --------------------------------------------------------------------------- who can see the money pages

def test_nobody_below_the_owner_can_open_any_of_it(test_client, owner, reviewer):
    plain = reader(test_client)
    for method, path, body in OWNER_ONLY:
        for who in (reviewer, plain, None):  # a reviewer is not enough; neither is any other reader, or nobody
            response = go(test_client, who, method, path, **({"json": body} if body else {}))
            assert response.status_code == 404, (method, path, response.status_code)
            assert response.json() == {"detail": "Not Found"}  # the same answer as an address that was never there
    assert go(test_client, owner, "get", "/payments").status_code == 200


def test_with_no_owner_listed_nobody_is_one(test_client, owner, reviewer):
    with patch("app.core.admin.settings.OWNER_EMAILS", ""):
        assert go(test_client, owner, "get", "/profit/2025-09").status_code == 404
        assert go(test_client, reviewer, "get", "/profit/2025-09").status_code == 404


def test_an_address_google_has_not_vouched_for_is_not_an_owner(test_client):
    stranger = _register_and_login(test_client)  # a password sign-up: unverified
    with patch("app.core.admin.settings.OWNER_EMAILS", stranger["user"]["email"]):
        assert go(test_client, stranger, "get", "/profit/2025-09").status_code == 404


def test_an_owner_is_also_a_reviewer_and_a_reviewer_is_not_an_owner(test_client, owner, reviewer):
    mine = test_client.get("/api/auth/me", headers=owner["headers"]).json()
    theirs = test_client.get("/api/auth/me", headers=reviewer["headers"]).json()
    assert (mine["is_owner"], mine["is_admin"]) == (True, True)   # the owner needs to be listed only once
    assert (theirs["is_owner"], theirs["is_admin"]) == (False, True)
    assert test_client.get("/api/earnings/months", headers=owner["headers"]).status_code == 200
    assert test_client.get("/api/earnings/months", headers=reviewer["headers"]).status_code == 200


def test_the_owners_settings_appear_in_nothing_anyone_else_can_read(test_client, owner, reviewer):
    writer = partner_writer(test_client)
    add_entry(test_client, owner, "income_sponsor", "777", "2026-06-10", "निजी आय")  # a month with real figures of its own
    go(test_client, owner, "put", "/profit/2026-06", json={"pool_percent": 37, "holdback_percent": 13, "note": "निजी नोट"})
    assert go(test_client, owner, "get", "/profit/2026-06").json()["suggested"] is not None
    responses = [
        test_client.get("/api/earnings/months", headers=reviewer["headers"]),
        test_client.get("/api/earnings/months/2026-06", headers=reviewer["headers"]),
        test_client.get("/api/earnings/balances", headers=reviewer["headers"]),
        test_client.get("/api/earnings/payouts", headers=reviewer["headers"]),
        test_client.get("/api/partner/rules"),
        test_client.get("/api/partner/me", headers=writer["headers"]),
        test_client.get("/api/partner/earnings", headers=writer["headers"]),
        test_client.get("/api/payments/config"),
        test_client.get(f"/api/users/{writer['user']['id']}"),
        test_client.get("/api/notifications", headers=writer["headers"]),
    ]
    for response in responses:
        assert response.status_code == 200, response.request.url
        text = response.text.lower()
        for word in ("pool_percent", "holdback", "profit", "निजी नोट", "निजी आय", "77700"):
            assert word not in text, (response.request.url, word)


# --------------------------------------------------------------------------- the monthly sheet

_september_is_seeded = []


def seed_september(client, owner):
    """A month (September 2025) with every kind of line: a membership, a tip, a sponsor, a bill.
    The books only ever grow, so this is done once however many tests ask for it."""
    if _september_is_seeded:
        return
    _september_is_seeded.append(True)
    member, supporter, writer = reader(client), reader(client), partner_writer(client)
    membership = client.post("/api/membership/checkout", json={"plan": "monthly", "auto_renew": False}, headers=member["headers"]).json()
    pay(client, membership["payment"]["public_id"], created_at=epoch(datetime(2025, 9, 5, 12)))
    tipped = tip(client, supporter, writer, "50").json()
    pay(client, tipped["payment"]["public_id"], created_at=epoch(datetime(2025, 9, 6, 12)))
    add_entry(client, owner, "income_sponsor", "20000", "2025-09-10", "प्रायोजक")
    add_entry(client, owner, "expense_hosting", "3000", "2025-09-12", "सर्वर")


def test_the_sheet_adds_up_what_came_in_and_what_it_cost(test_client, owner):
    seed_september(test_client, owner)
    s = sheet(test_client, owner, "2025-09")
    assert s["income"]["income_membership"] == 4900 and s["income"]["income_tip_fee"] == 500
    assert s["income"]["income_sponsor"] == 2_000_000 and s["income"]["income_grant"] == 0
    assert s["costs"]["expense_hosting"] == 300_000 and s["costs"]["expense_provider_fee"] == 116 + 118
    assert s["income_total"] == 2_005_400 and s["cost_total"] == 300_234
    assert s["profit"] == 1_705_166
    # Tips are not the site's money: the gross and the writers' share are shown, but only the site's cut is income.
    assert s["tips_gross"] == 5000 and s["tips_to_writers"] == 4500
    assert s["balanced"] is True and s["ended"] is True
    assert s["suggested"] is None and s["choice"]["pool_percent"] is None  # nothing is suggested until a percent is chosen


def test_a_chosen_percent_gives_a_suggestion_in_whole_rupees(test_client, owner):
    seed_september(test_client, owner)
    chosen = go(test_client, owner, "put", "/profit/2025-09", json={"pool_percent": 40, "holdback_percent": 10, "note": "पहला महीना"})
    s = chosen.json()
    # 40% of 1,705,166 is 682,066.4 paise: down to the rupee, 682,000. A tenth of that is held back.
    assert (s["pool_before_holdback"], s["held_back"], s["suggested"]) == (682_000, 68_200, 613_800)
    assert s["choice"] == {"pool_percent": 40, "holdback_percent": 10, "note": "पहला महीना", "own": True}


def test_the_held_back_part_is_added_to_the_next_months_suggestion(test_client, owner):
    seed_september(test_client, owner)
    go(test_client, owner, "put", "/profit/2025-09", json={"pool_percent": 40, "holdback_percent": 10})
    s = sheet(test_client, owner, "2025-10")  # October has no money of its own and no choice of its own
    assert s["profit"] == 0 and s["choice"]["own"] is False and s["choice"]["pool_percent"] == 40  # the last choice carries on
    assert s["carried_in"] == 68_200 and s["suggested"] == 68_200


def test_a_month_at_a_loss_has_no_pool_and_does_not_carry_the_loss(test_client, owner):
    go(test_client, owner, "put", "/profit/2025-12", json={"pool_percent": 40})
    add_entry(test_client, owner, "expense_hosting", "500", "2025-12-03")
    december = sheet(test_client, owner, "2025-12")
    assert december["profit"] == -50_000 and december["pool_before_holdback"] == 0 and december["suggested"] == 0
    add_entry(test_client, owner, "income_grant", "1000", "2026-01-04")
    january = sheet(test_client, owner, "2026-01")
    assert january["profit"] == 100_000 and january["pool_before_holdback"] == 40_000  # December's loss is not taken off


def test_a_yearly_membership_is_listed_on_its_own_line(test_client, owner):
    member = reader(test_client)
    started = test_client.post("/api/membership/checkout", json={"plan": "yearly", "auto_renew": False}, headers=member["headers"]).json()
    pay(test_client, started["payment"]["public_id"], created_at=epoch(datetime(2025, 11, 3, 12)))
    s = sheet(test_client, owner, "2025-11")
    assert s["yearly_received"] == 39_900 and s["income"]["income_membership"] == 39_900  # counted when received


def test_the_choice_is_validated_and_can_be_cleared(test_client, owner):
    for body in ({"pool_percent": 101}, {"pool_percent": -1}, {"holdback_percent": 51}, {"holdback_percent": -1}):
        assert go(test_client, owner, "put", "/profit/2025-09", json=body).status_code == 422, body
    cleared = go(test_client, owner, "put", "/profit/2025-09", json={}).json()
    assert cleared["suggested"] is None and cleared["choice"]["pool_percent"] is None
    assert go(test_client, owner, "put", "/profit/2099-01", json={"pool_percent": 5}).status_code == 404  # the future
    assert go(test_client, owner, "get", "/profit/not-a-month").status_code == 404


def test_applying_the_suggestion_sets_only_the_pool(test_client, owner, reviewer):
    seed_september(test_client, owner)
    go(test_client, owner, "put", "/profit/2025-09", json={"pool_percent": 40, "holdback_percent": 10})
    reviewer_set = test_client.put(
        "/api/earnings/months/2025-09",
        json={"pool_rupees": 5, "max_per_point_rupees": 2, "member_weight": 2, "nonmember_weight": 1},
        headers=reviewer["headers"],
    )
    assert reviewer_set.status_code == 200

    applied = go(test_client, owner, "post", "/profit/2025-09/apply").json()
    assert applied["pool_set"] == 613_800
    month = test_client.get("/api/earnings/months/2025-09", headers=reviewer["headers"]).json()
    assert month["pool_paise"] == 613_800 and month["max_per_point_paise"] == 200
    assert (month["member_weight"], month["nonmember_weight"]) == (2, 1)  # what the desk set is untouched

    go(test_client, owner, "put", "/profit/2025-09", json={})
    assert go(test_client, owner, "post", "/profit/2025-09/apply").status_code == 400  # nothing to apply

    go(test_client, owner, "put", "/profit/2025-09", json={"pool_percent": 40})
    with SessionLocal() as db:
        db.query(EarningMonth).filter(EarningMonth.month == "2025-09").update({"finalized_at": datetime(2025, 10, 5)})
        db.commit()
    assert go(test_client, owner, "post", "/profit/2025-09/apply").status_code == 400  # a settled month stays settled


# --------------------------------------------------------------------------- typing money in by hand

def test_hand_entered_money_lands_in_the_month_it_is_dated(test_client, owner):
    before = sheet(test_client, owner, "2025-09")["profit"]
    entry = add_entry(test_client, owner, "income_other", "123.45", "2025-09-30", "किताब की रॉयल्टी")
    assert entry["kind"] == "manual" and entry["memo"] == "किताब की रॉयल्टी"
    assert {(e["account"], e["amount_paise"]) for e in entry["entries"]} == {("cash", 12345), ("income_other", -12345)}
    assert sheet(test_client, owner, "2025-09")["profit"] == before + 12345
    # The last day of a month in Indian time still belongs to that month, not the next.
    assert sheet(test_client, owner, "2025-10")["income"]["income_other"] == 0


def test_only_the_accounts_the_owner_may_type_into_are_open(test_client, owner):
    for account in ("income_membership", "income_tip_fee", "expense_provider_fee", "cash", "owed_to_writers", "made_up"):
        assert go(test_client, owner, "post", "/books/entries", json={"account": account, "amount_rupees": "5"}).status_code == 400, account
    for amount in ("0", "-5", "10000001", "1.234"):
        assert go(test_client, owner, "post", "/books/entries", json={"account": "income_other", "amount_rupees": amount}).status_code == 422, amount
    assert go(test_client, owner, "post", "/books/entries", json={"account": "income_other", "amount_rupees": "5", "on": "2099-01-01"}).status_code == 400
    assert go(test_client, owner, "post", "/books/entries", json={"account": "income_other", "amount_rupees": "5", "on": "2019-01-01"}).status_code == 400


def test_a_wrong_entry_is_undone_in_its_own_month_and_never_deleted(test_client, owner):
    before = sheet(test_client, owner, "2025-08")["profit"]
    wrong = add_entry(test_client, owner, "expense_other", "9999", "2025-08-15", "गलती")
    assert sheet(test_client, owner, "2025-08")["profit"] == before - 999_900

    undone = go(test_client, owner, "post", f"/books/{wrong['id']}/reverse")
    assert undone.status_code == 200 and undone.json()["reverses_id"] == wrong["id"]
    assert sheet(test_client, owner, "2025-08")["profit"] == before  # corrected where it was wrong
    assert go(test_client, owner, "post", f"/books/{wrong['id']}/reverse").status_code == 400  # not twice

    listing = go(test_client, owner, "get", "/books", params={"month": "2025-08"}).json()
    by_id = {item["id"]: item for item in listing["items"]}
    assert by_id[wrong["id"]]["reversed"] is True and by_id[undone.json()["id"]]["reverses_id"] == wrong["id"]


def test_money_the_provider_moved_cannot_be_edited_away_by_hand(test_client, owner):
    seed_september(test_client, owner)
    listing = go(test_client, owner, "get", "/books", params={"month": "2025-09"}).json()
    captured = next(item for item in listing["items"] if item["kind"] in ("tip", "membership"))
    refused = go(test_client, owner, "post", f"/books/{captured['id']}/reverse")
    assert refused.status_code == 400
    assert go(test_client, owner, "post", "/books/999999999/reverse").status_code == 404


# --------------------------------------------------------------------------- the books and the payment log

def test_the_books_list_a_months_transactions_and_say_they_balance(test_client, owner):
    seed_september(test_client, owner)
    listing = go(test_client, owner, "get", "/books", params={"month": "2025-09"}).json()
    assert listing["balanced"] is True and listing["month"] == "2025-09"
    kinds = {item["kind"] for item in listing["items"]}
    assert {"tip", "membership", "provider_fee", "manual"} <= kinds
    for item in listing["items"]:
        assert sum(e["amount_paise"] for e in item["entries"]) == 0  # every transaction balances
    assert listing["cash_paise"] == balance(CASH)
    assert go(test_client, owner, "get", "/books", params={"month": "2099-01"}).status_code == 404
    assert go(test_client, owner, "get", "/books").json()["month"]  # default: this month


def test_the_payment_log_shows_what_needs_a_look(test_client, owner):
    payer, writer = reader(test_client), partner_writer(test_client)
    pay(test_client, tip(test_client, payer, writer, "20").json()["payment"]["public_id"])  # one that went through
    started = tip(test_client, payer, writer, "50").json()
    send(test_client, started["payment"]["public_id"], PAYMENT_CAPTURED, amount=100)  # and one whose wrong amount arrives

    log = go(test_client, owner, "get", "/payments").json()
    assert log["connected"] is True and log["provider"] == "sandbox" and log["test_mode"] is True
    assert log["counts"].get("created", 0) >= 1 and log["counts"].get("captured", 0) >= 1
    assert any(p["status"] == "rejected" and "100" in p["detail"] for p in log["problems"])
    mine = next(row for row in log["recent"] if row["public_id"] == started["payment"]["public_id"])
    assert mine["payer"]["username"] == payer["user"]["username"] and mine["target"]["username"] == writer["user"]["username"]
    assert mine["failure_reason"]

    with patch.object(settings, "PAYMENTS_PROVIDER", ""):
        off = go(test_client, owner, "get", "/payments").json()
        assert off["connected"] is False and off["provider"] is None
