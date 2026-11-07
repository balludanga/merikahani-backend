"""Member-weighted points: a reader's reaction is worth more (or less) depending on whether they held a
membership at that moment. With the default of 1 and 1 nothing changes."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.earnings import engagement, weights_for
from app.database import SessionLocal
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.highlight import Highlight
from app.models.partner import APPROVED, EarningLine, EarningMonth, PartnerApplication, Payout
from app.models.payments import Payment, MembershipPeriod
from app.models.user import User
from tests.conftest import _register_and_login
from tests.money_helpers import wipe_programme_books
from tests.test_posts import _create_post

ADMIN_EMAIL = "testuser@example.com"
_made_users = []


@pytest.fixture(autouse=True)
def _admin_is_listed():
    with patch("app.core.admin.settings.ADMIN_EMAILS", ADMIN_EMAIL):
        yield


def _wipe():
    wipe_programme_books()
    db = SessionLocal()
    try:
        for model in (Payout, EarningLine, EarningMonth, PartnerApplication):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def _clean_slate():
    _wipe()
    yield
    _wipe()
    db = SessionLocal()
    try:
        if _made_users:
            for model, column in ((Clap, Clap.user_id), (Highlight, Highlight.user_id), (Comment, Comment.author_id)):
                db.query(model).filter(column.in_(_made_users)).delete(synchronize_session=False)
            db.query(MembershipPeriod).filter(MembershipPeriod.user_id.in_(_made_users)).delete(synchronize_session=False)
            db.query(Payment).filter(Payment.user_id.in_(_made_users)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(_made_users)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def admin(test_client):
    google = test_client.post("/api/auth/google-login", json={"token": "a" * 80})
    assert google.status_code == 200, google.text
    return {"headers": {"Authorization": f"Bearer {google.json()['access_token']}"}, "user": google.json()["user"]}


# --------------------------------------------------------------------------- seeding

def at(month, day, hour=12, year=2025):
    return datetime(year, month, day, hour)


def established_reader():
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:8]
        user = User(email=f"mp{tag}@example.com", username=f"mp{tag}", hashed_password="x", created_at=datetime(2024, 6, 1))
        db.add(user)
        db.commit()
        _made_users.append(user.id)
        return user.id
    finally:
        db.close()


def member_between(user_id, start, end):
    """Give the reader a paid period [start, end)."""
    db = SessionLocal()
    try:
        paid = Payment(public_id=uuid.uuid4().hex, user_id=user_id, purpose="membership", status="captured", amount_paise=4900,
                       provider="sandbox", plan_code="monthly", captured_at=start)
        db.add(paid)
        db.flush()
        db.add(MembershipPeriod(user_id=user_id, payment_id=paid.id, starts_at=start, ends_at=end))
        db.commit()
    finally:
        db.close()


def partner(test_client, stories=1, approved=datetime(2025, 1, 1)):
    who = _register_and_login(test_client)
    who["post_ids"] = [_create_post(test_client, who["headers"], f"वज़न की रचना {uuid.uuid4().hex[:6]}")["id"] for _ in range(stories)]
    db = SessionLocal()
    try:
        db.add(PartnerApplication(user_id=who["user"]["id"], status=APPROVED, applied_at=approved - timedelta(days=3), approved_at=approved))
        db.commit()
    finally:
        db.close()
    return who


def add(*rows):
    db = SessionLocal()
    try:
        db.add_all(rows)
        db.commit()
    finally:
        db.close()


def clap(reader_id, post_id, when):
    return Clap(user_id=reader_id, post_id=post_id, count=1, created_at=when, updated_at=when)


def tally(month, user_id, member_weight=None, nonmember_weight=None):
    with SessionLocal() as db:
        return engagement(db, month, member_weight, nonmember_weight)[user_id]


# --------------------------------------------------------------------------- the weights

def test_the_default_changes_nothing(test_client):
    who = partner(test_client)
    ordinary, paying = established_reader(), established_reader()
    member_between(paying, at(2, 1), at(4, 1))
    add(clap(ordinary, who["post_ids"][0], at(3, 5)), clap(paying, who["post_ids"][0], at(3, 6)))
    with SessionLocal() as db:
        assert weights_for(None) == (1, 1) == (settings.POINTS_MEMBER_WEIGHT, settings.POINTS_NONMEMBER_WEIGHT)
    t = tally("2025-03", who["user"]["id"])
    assert t.claps == 2 and t.points == 2 and t.member_points == 1 and t.readers == 2


def test_a_members_reaction_is_worth_the_months_member_weight(test_client):
    who = partner(test_client, stories=2)
    ordinary, paying = established_reader(), established_reader()
    member_between(paying, at(2, 1), at(4, 1))
    p1, p2 = who["post_ids"]
    add(
        clap(ordinary, p1, at(3, 5)),
        clap(paying, p1, at(3, 6)),
        Highlight(user_id=paying, post_id=p2, text="अंश", created_at=at(3, 7)),
        Comment(content="सुंदर", post_id=p2, author_id=ordinary, created_at=at(3, 8), updated_at=at(3, 8)),
    )
    t = tally("2025-03", who["user"]["id"], member_weight=3, nonmember_weight=1)
    # ordinary: clap 1 + response 5 = 6; member: clap 1*3 + highlight 2*3 = 9
    assert t.points == 6 + 9 and t.member_points == 9
    assert (t.claps, t.highlights, t.responses) == (2, 1, 1)
    assert t.by_reader == {ordinary: 6, paying: 9}


def test_a_reaction_worth_nothing_is_not_counted_at_all(test_client):
    who = partner(test_client, stories=2)
    ordinary, paying = established_reader(), established_reader()
    member_between(paying, at(2, 1), at(4, 1))
    p1, p2 = who["post_ids"]
    add(
        clap(ordinary, p1, at(3, 5)),
        clap(paying, p1, at(3, 6)),
        Highlight(user_id=ordinary, post_id=p2, text="अंश", created_at=at(3, 7)),
        Comment(content="सुंदर", post_id=p2, author_id=ordinary, created_at=at(3, 8), updated_at=at(3, 8)),
    )
    t = tally("2025-03", who["user"]["id"], member_weight=1, nonmember_weight=0)
    assert (t.claps, t.highlights, t.responses) == (1, 0, 0)  # only the member's clap shows up anywhere
    assert t.points == 1 and t.member_points == 1 and t.readers == 1 and set(t.by_reader) == {paying}


def test_membership_is_judged_at_the_moment_of_each_reaction(test_client):
    who = partner(test_client, stories=3)
    reader_id = established_reader()
    member_between(reader_id, at(3, 10), at(3, 20))
    p1, p2, p3 = who["post_ids"]
    add(clap(reader_id, p1, at(3, 5)), clap(reader_id, p2, at(3, 15)), clap(reader_id, p3, at(3, 25)))
    t = tally("2025-03", who["user"]["id"], member_weight=2, nonmember_weight=1)
    assert t.points == 1 + 2 + 1 and t.member_points == 2  # before, during, after the membership


def test_a_story_cap_keeps_the_earliest_reactions_and_weighs_each_by_its_own_time(test_client):
    who = partner(test_client)
    reader_id = established_reader()
    member_between(reader_id, at(3, 12), at(3, 30))  # starts between the 2nd and 3rd highlight
    post_id = who["post_ids"][0]
    add(*[Highlight(user_id=reader_id, post_id=post_id, text=f"अंश {day}", created_at=at(3, day)) for day in (6, 9, 15, 18)])
    t = tally("2025-03", who["user"]["id"], member_weight=2, nonmember_weight=1)
    # Only the first three count (Mar 6, 9, 15): 2 + 2 + 2*2. Keeping the LAST three would give 2 + 4 + 4.
    assert t.highlights == 3 and t.points == 8 and t.member_points == 4


def test_the_existing_rules_still_hold_with_weights_on(test_client):
    """Weighting multiplies points; it never lets in what the rules keep out."""
    who = partner(test_client)
    paying = established_reader()
    member_between(paying, at(1, 1), at(6, 1))
    fresh = established_reader()
    with SessionLocal() as db:
        db.query(User).filter(User.id == fresh).update({"created_at": at(3, 3)})  # joined 2 days before acting
        db.commit()
    member_between(fresh, at(1, 1), at(6, 1))
    add(clap(paying, who["post_ids"][0], at(3, 5)), clap(fresh, who["post_ids"][0], at(3, 5)), clap(who["user"]["id"], who["post_ids"][0], at(3, 5)))
    t = tally("2025-03", who["user"]["id"], member_weight=5, nonmember_weight=5)
    assert t.claps == 1 and t.points == 5  # not the brand-new account, not the writer's own clap


# --------------------------------------------------------------------------- the desk, and finalizing

def team(test_client, admin, method, path, **kwargs):
    return getattr(test_client, method)(f"/api/earnings{path}", headers=admin["headers"], **kwargs)


def test_a_months_own_weights_are_used_shown_and_frozen_at_finalizing(test_client, admin):
    who = partner(test_client)
    uid = who["user"]["id"]
    ordinary, paying = established_reader(), established_reader()
    member_between(paying, at(3, 1), at(5, 1))
    add(clap(ordinary, who["post_ids"][0], at(4, 5)), Highlight(user_id=paying, post_id=who["post_ids"][0], text="अंश", created_at=at(4, 9)))

    pool = team(test_client, admin, "put", "/months/2025-04", json={"pool_rupees": 30, "member_weight": 2, "nonmember_weight": 1})
    assert pool.status_code == 200
    detail = pool.json()
    assert (detail["member_weight"], detail["nonmember_weight"]) == (2, 1)
    row = next(r for r in detail["rows"] if r["user"]["id"] == uid)
    assert row["points"] == 1 + 2 * 2 and row["member_points"] == 4 and row["amount_paise"] == 3000

    final = team(test_client, admin, "post", "/months/2025-04/finalize", json={"exclude": []}).json()
    assert final["preview"] is False and (final["member_weight"], final["nonmember_weight"]) == (2, 1)

    # What the site-wide setting becomes later cannot rewrite a month that is done.
    with patch.object(settings, "POINTS_MEMBER_WEIGHT", 7):
        again = team(test_client, admin, "get", "/months/2025-04").json()
    assert (again["member_weight"], again["nonmember_weight"]) == (2, 1)
    line = next(r for r in again["rows"] if r["user"]["id"] == uid)
    assert line["points"] == 5 and line["member_points"] == 4

    statement = test_client.get("/api/partner/earnings", headers=who["headers"]).json()
    assert statement["months"][0]["member_points"] == 4 and statement["pool_paise"] == 3000 and statement["tips_paise"] == 0


def test_a_month_with_no_weights_of_its_own_freezes_the_ones_in_force(test_client, admin):
    who = partner(test_client)
    add(clap(established_reader(), who["post_ids"][0], at(5, 5)))
    team(test_client, admin, "put", "/months/2025-05", json={"pool_rupees": 10})
    with patch.object(settings, "POINTS_MEMBER_WEIGHT", 4), patch.object(settings, "POINTS_NONMEMBER_WEIGHT", 2):
        final = team(test_client, admin, "post", "/months/2025-05/finalize", json={"exclude": []}).json()
        line = next(r for r in final["rows"] if r["user"]["id"] == who["user"]["id"])
        assert (final["member_weight"], final["nonmember_weight"]) == (4, 2) and line["points"] == 2  # a clap: 1 point x 2
    with patch.object(settings, "POINTS_NONMEMBER_WEIGHT", 9):
        assert team(test_client, admin, "get", "/months/2025-05").json()["nonmember_weight"] == 2


def test_the_weights_are_checked(test_client, admin):
    for body in ({"member_weight": 0}, {"member_weight": 11}, {"nonmember_weight": -1}, {"nonmember_weight": 11}):
        assert team(test_client, admin, "put", "/months/2025-06", json={"pool_rupees": 5, **body}).status_code == 422, body
    ok = team(test_client, admin, "put", "/months/2025-06", json={"pool_rupees": 5, "member_weight": 1, "nonmember_weight": 0})
    assert ok.status_code == 200 and ok.json()["nonmember_weight"] == 0
    cleared = team(test_client, admin, "put", "/months/2025-06", json={"pool_rupees": 5})  # empty = back to the site-wide setting
    assert (cleared.json()["member_weight"], cleared.json()["nonmember_weight"]) == (1, 1)


def test_the_page_may_quote_the_weights_in_force(test_client, admin):
    from app.core.earnings import current_month

    assert test_client.get("/api/partner/rules").json()["member_weight"] == 1
    assert test_client.get("/api/payments/config").json()["enabled"] is False  # no provider, nothing to show
    key = current_month()
    team(test_client, admin, "put", f"/months/{key}", json={"pool_rupees": 1, "member_weight": 3, "nonmember_weight": 1})
    rules = test_client.get("/api/partner/rules").json()
    assert (rules["member_weight"], rules["nonmember_weight"]) == (3, 1)
    with patch.object(settings, "PAYMENTS_PROVIDER", "sandbox"):
        assert test_client.get("/api/payments/config").json()["membership"]["weighted_points"] is True
