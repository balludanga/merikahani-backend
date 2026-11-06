"""Write and earn: what counts, how a month's pool is shared, finalizing it, and paying out."""

import random
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.core.clock import utcnow
from app.core.earnings import (
    current_month,
    engagement,
    month_bounds,
    month_has_ended,
    month_key,
    previous_month,
    share_out,
    valid_month,
)
from app.database import SessionLocal
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.highlight import Highlight
from app.models.notification import Notification
from app.models.partner import APPROVED, PENDING, REVOKED, EarningLine, EarningMonth, PartnerApplication, Payout
from app.models.user import User
from tests.conftest import _register_and_login
from tests.test_posts import _create_post

ADMIN_EMAIL = "testuser@example.com"  # the address the mocked Google reply returns
_made_users = []  # readers created straight in the database, removed at the end


@pytest.fixture(autouse=True)
def _admin_is_listed():
    with patch("app.core.admin.settings.ADMIN_EMAILS", ADMIN_EMAIL):
        yield


def _wipe():
    db = SessionLocal()
    try:
        for model in (Payout, EarningLine, EarningMonth, PartnerApplication):
            db.query(model).delete()
        db.query(Notification).filter(Notification.kind == "payout_paid").delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def _clean_slate():
    """The test database is shared by the whole run: start and finish with an empty programme."""
    _wipe()
    yield
    _wipe()
    db = SessionLocal()
    try:
        if _made_users:
            for model, column in ((Clap, Clap.user_id), (Highlight, Highlight.user_id), (Comment, Comment.author_id)):
                db.query(model).filter(column.in_(_made_users)).delete(synchronize_session=False)
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

def at(month, day, hour=12, minute=0, second=0):
    """A moment in 2026, as the naive UTC the database stores."""
    return datetime(2026, month, day, hour, minute, second)


def reader(created=datetime(2026, 1, 1)):
    """An established reader account (nobody signs in as them)."""
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:8]
        user = User(email=f"rd{tag}@example.com", username=f"rd{tag}", hashed_password="x", created_at=created)
        db.add(user)
        db.commit()
        _made_users.append(user.id)
        return user.id
    finally:
        db.close()


def partner(test_client, status=APPROVED, approved=datetime(2026, 2, 1), stories=1):
    """A real account with public stories and a partner row. ``who["post_ids"]`` lists the stories."""
    who = _register_and_login(test_client)
    who["post_ids"] = [
        _create_post(test_client, who["headers"], f"कमाई की रचना {uuid.uuid4().hex[:6]}")["id"] for _ in range(stories)
    ]
    db = SessionLocal()
    try:
        db.add(
            PartnerApplication(
                user_id=who["user"]["id"],
                status=status,
                applied_at=approved - timedelta(days=3),
                approved_at=approved if status in (APPROVED, REVOKED) else None,
                followers_at_apply=200,
                stories_at_apply=1,
            )
        )
        db.commit()
    finally:
        db.close()
    return who


def add(*rows):
    """Store rows and return their ids."""
    db = SessionLocal()
    try:
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]
    finally:
        db.close()


def clap(who, post_id, when):
    return Clap(user_id=who, post_id=post_id, count=1, created_at=when, updated_at=when)


def highlight(who, post_id, when):
    return Highlight(user_id=who, post_id=post_id, text="एक अंश", created_at=when)


def response(who, post_id, when, parent_id=None):
    return Comment(content="सुंदर", post_id=post_id, author_id=who, parent_id=parent_id, created_at=when, updated_at=when)


def tally(month, user_id):
    with SessionLocal() as db:
        return engagement(db, month)[user_id]


def team(test_client, admin, method, path, **kwargs):
    return getattr(test_client, method)(f"/api/earnings{path}", headers=admin["headers"], **kwargs)


def row_for(detail, user_id):
    return next(row for row in detail["rows"] if row["user"]["id"] == user_id)


# --------------------------------------------------------------------------- months (Indian time)

def test_a_month_runs_on_indian_time():
    start, end = month_bounds("2026-10")
    assert start == datetime(2026, 9, 30, 18, 30) and end == datetime(2026, 10, 31, 18, 30)
    assert month_bounds("2026-12")[1] == datetime(2026, 12, 31, 18, 30)  # December rolls into January
    assert month_key(datetime(2026, 10, 31, 18, 29, 59)) == "2026-10"
    assert month_key(datetime(2026, 10, 31, 18, 30)) == "2026-11"  # 00:00 on 1 November in India
    assert previous_month("2026-01") == "2025-12" and previous_month("2026-03") == "2026-02"


def test_which_months_are_real():
    assert valid_month("2026-03") and valid_month("2024-12")
    for bad in ("2026-13", "2026-00", "2026-3", "26-03", "abc", "", "2023-12", "2026-03-01"):
        assert not valid_month(bad), bad
    assert month_has_ended("2026-03") and not month_has_ended(current_month())


# --------------------------------------------------------------------------- sharing the pool

def test_the_pool_is_split_by_points_and_every_paisa_is_placed():
    assert share_out({1: 6, 2: 2}, 100_000) == {1: 75_000, 2: 25_000}
    # Three equal shares of 100 paise: the odd paisa goes to the lowest id, the same on every run.
    assert share_out({1: 1, 2: 1, 3: 1}, 100) == {1: 34, 2: 33, 3: 33}
    assert share_out({1: 0, 2: 0}, 5_000) == {1: 0, 2: 0}  # nobody earned anything
    assert share_out({1: 5}, 0) == {1: 0}


def test_split_always_adds_up_to_the_pool():
    rng = random.Random(7)
    for _ in range(400):
        points = {uid: rng.choice([0, 0, 1, 2, 7, 30, 500]) for uid in range(1, rng.randint(1, 9))}
        pool = rng.randint(0, 20_000_000)
        amounts = share_out(points, pool)
        assert set(amounts) == set(points) and all(a >= 0 for a in amounts.values())
        total = sum(points.values())
        if total and pool:
            assert sum(amounts.values()) == pool
            for uid, p in points.items():
                assert abs(amounts[uid] - pool * p / total) < 1  # never more than a paisa from exact
        else:
            assert sum(amounts.values()) == 0


def test_a_left_out_writer_gets_nothing_and_the_rest_share_the_pool():
    amounts = share_out({1: 3, 2: 1, 3: 1}, 1_000, excluded=[1])
    assert amounts == {1: 0, 2: 500, 3: 500}


def test_a_ceiling_per_point_caps_the_share_and_does_not_hand_the_rest_on():
    assert share_out({1: 3}, 10_000, max_per_point_paise=100) == {1: 300}
    assert share_out({1: 1, 2: 1}, 1_000, max_per_point_paise=100) == {1: 100, 2: 100}
    assert share_out({1: 1, 2: 1}, 1_000, max_per_point_paise=10_000) == {1: 500, 2: 500}  # not reached


# --------------------------------------------------------------------------- what counts

def test_claps_highlights_and_responses_count_with_caps(test_client):
    owner = partner(test_client)
    post = owner["post_ids"][0]
    r1, r2 = reader(), reader()
    first_response = add(response(r1, post, at(3, 5)))[0]
    add(
        clap(r1, post, at(3, 5)),
        *[highlight(r1, post, at(3, 6, hour)) for hour in range(8, 13)],  # five highlights: three count
        response(r1, post, at(3, 7)),
        response(r1, post, at(3, 8)),  # three responses: two count
        clap(r2, post, at(3, 9)),
        response(r2, post, at(3, 10), parent_id=first_response),  # a reply is a response too
    )
    t = tally("2026-03", owner["user"]["id"])
    assert (t.claps, t.highlights, t.responses) == (2, 3, 3)
    assert t.points == 2 * 1 + 3 * 2 + 3 * 5 == 23
    assert t.readers == 2
    assert t.by_reader == {r1: 17, r2: 6} and t.top_reader_pct == 74


def test_what_should_not_count_does_not(test_client):
    owner = partner(test_client, approved=at(3, 10, 0))
    post = owner["post_ids"][0]
    uid = owner["user"]["id"]
    draft = _create_post(test_client, owner["headers"], f"निजी मसौदा {uuid.uuid4().hex[:6]}", published=0)["id"]
    add(
        clap(reader(), post, at(3, 12)),  # the one that counts
        clap(uid, post, at(3, 12)),  # the writer's own clap
        response(uid, post, at(3, 12)),  # the writer answering a reader
        clap(reader(created=at(3, 8)), post, at(3, 12)),  # an account only four days old
        highlight(reader(), post, at(3, 5)),  # before they became a partner
        response(reader(), post, at(2, 20)),  # last month
        clap(reader(), draft, at(3, 12)),  # on a story that is not public
    )
    t = tally("2026-03", uid)
    assert (t.claps, t.highlights, t.responses) == (1, 0, 0)

    # An account exactly a week old at the time counts; one second short of it does not.
    added = reader(created=at(3, 5, 12))
    near = reader(created=at(3, 5, 12, 0, 1))
    add(clap(added, post, at(3, 12)), clap(near, post, at(3, 12)))
    assert tally("2026-03", uid).claps == 2


def test_a_month_ends_at_midnight_in_india(test_client):
    owner = partner(test_client)
    post = owner["post_ids"][0]
    add(
        clap(reader(), post, at(2, 28, 18, 29, 59)),  # still February in India
        clap(reader(), post, at(2, 28, 18, 30)),  # 00:00 on 1 March: counts for March
        clap(reader(), post, at(3, 31, 18, 29, 59)),  # 23:59:59 on 31 March: counts for March
        clap(reader(), post, at(3, 31, 18, 30)),  # 00:00 on 1 April: not March
    )
    assert tally("2026-03", owner["user"]["id"]).claps == 2
    assert tally("2026-02", owner["user"]["id"]).claps == 1


def test_only_current_partners_are_in_the_tally(test_client):
    pending = partner(test_client, status=PENDING)
    removed = partner(test_client, status=REVOKED)
    live = partner(test_client)
    add(
        clap(reader(), pending["post_ids"][0], at(3, 12)),
        clap(reader(), removed["post_ids"][0], at(3, 12)),
        clap(reader(), live["post_ids"][0], at(3, 12)),
    )
    with SessionLocal() as db:
        tallies = engagement(db, "2026-03")
    assert live["user"]["id"] in tallies
    assert pending["user"]["id"] not in tallies and removed["user"]["id"] not in tallies


def test_a_deleted_response_stops_counting(test_client):
    owner = partner(test_client)
    post = owner["post_ids"][0]
    who = reader()
    [comment] = add(response(who, post, at(3, 12)))
    assert tally("2026-03", owner["user"]["id"]).responses == 1
    db = SessionLocal()
    try:
        db.query(Comment).filter(Comment.id == comment).delete()
        db.commit()
    finally:
        db.close()
    assert tally("2026-03", owner["user"]["id"]).responses == 0


# --------------------------------------------------------------------------- the month, end to end

def test_set_the_pool_preview_finalize_and_the_writer_reads_the_statement(test_client, admin):
    month = "2026-08"
    a, b = partner(test_client), partner(test_client)
    r1, r2, r3 = reader(), reader(), reader()
    add(
        clap(r1, a["post_ids"][0], at(8, 5)), response(r1, a["post_ids"][0], at(8, 6)),  # A: 1 + 5 = 6
        clap(r2, b["post_ids"][0], at(8, 7)), clap(r3, b["post_ids"][0], at(8, 8)),  # B: 2
    )
    aid, bid = a["user"]["id"], b["user"]["id"]

    nothing_yet = team(test_client, admin, "get", f"/months/{month}")
    assert nothing_yet.status_code == 200 and nothing_yet.json()["pool_paise"] == 0 and nothing_yet.json()["preview"] is True

    pool = team(test_client, admin, "put", f"/months/{month}", json={"pool_rupees": 1000})
    assert pool.status_code == 200, pool.text
    preview = pool.json()
    assert preview["pool_paise"] == 100_000 and preview["preview"] is True and preview["finalized"] is False
    assert row_for(preview, aid)["amount_paise"] == 75_000 and row_for(preview, aid)["points"] == 6
    assert row_for(preview, bid)["amount_paise"] == 25_000 and row_for(preview, bid)["readers"] == 2
    assert row_for(preview, aid)["top_reader_pct"] == 100  # one account (r1) gave all of A's points
    assert row_for(preview, bid)["top_reader_pct"] == 50  # two accounts gave B one point each
    assert "example.com" not in str(preview)

    done = team(test_client, admin, "post", f"/months/{month}/finalize", json={})
    assert done.status_code == 200, done.text
    final = done.json()
    assert final["finalized"] is True and final["preview"] is False
    assert final["total_points"] >= 8 and final["distributed_paise"] == 100_000
    assert row_for(final, aid)["amount_paise"] == 75_000

    # It is final: no second finalize, no new pool, and later engagement does not change the stored result.
    assert team(test_client, admin, "post", f"/months/{month}/finalize", json={}).status_code == 400
    assert team(test_client, admin, "put", f"/months/{month}", json={"pool_rupees": 5}).status_code == 400
    add(clap(reader(), b["post_ids"][0], at(8, 20)))
    assert row_for(team(test_client, admin, "get", f"/months/{month}").json(), bid)["amount_paise"] == 25_000

    statement = test_client.get("/api/partner/earnings", headers=a["headers"]).json()
    assert statement["balance_paise"] == 75_000 and statement["earned_paise"] == 75_000 and statement["paid_paise"] == 0
    assert statement["months"] == [
        {"month": month, "claps": 1, "highlights": 0, "responses": 1, "readers": 1, "points": 6,
         "amount_paise": 75_000, "held_reason": None}
    ]
    listing = team(test_client, admin, "get", "/months").json()
    assert listing["current"] == current_month()
    assert any(m["month"] == month and m["finalized"] for m in listing["months"])
    assert {m["month"] for m in listing["months"]} >= {current_month(), previous_month(current_month())}


def test_leaving_a_writer_out_moves_their_share_to_the_others(test_client, admin):
    month = "2026-09"
    a, b = partner(test_client), partner(test_client)
    add(clap(reader(), a["post_ids"][0], at(9, 5)), clap(reader(), b["post_ids"][0], at(9, 6)))
    aid, bid = a["user"]["id"], b["user"]["id"]
    team(test_client, admin, "put", f"/months/{month}", json={"pool_rupees": 100})

    equal = team(test_client, admin, "get", f"/months/{month}").json()
    assert row_for(equal, aid)["amount_paise"] == 5_000 and row_for(equal, bid)["amount_paise"] == 5_000
    previewed = team(test_client, admin, "get", f"/months/{month}?exclude={aid}").json()
    assert row_for(previewed, aid)["amount_paise"] == 0 and row_for(previewed, bid)["amount_paise"] == 10_000

    blank = team(test_client, admin, "post", f"/months/{month}/finalize", json={"exclude": [{"user_id": aid, "reason": ""}]})
    assert blank.status_code == 422  # leaving someone out needs a reason
    final = team(
        test_client, admin, "post", f"/months/{month}/finalize",
        json={"exclude": [{"user_id": aid, "reason": "एक ही खाते से सारी तालियाँ"}]},
    ).json()
    assert row_for(final, aid)["amount_paise"] == 0 and row_for(final, aid)["held_reason"] == "एक ही खाते से सारी तालियाँ"
    assert row_for(final, bid)["amount_paise"] == 10_000

    held = test_client.get("/api/partner/earnings", headers=a["headers"]).json()
    assert held["months"][0]["held_reason"] == "एक ही खाते से सारी तालियाँ" and held["balance_paise"] == 0


def test_a_ceiling_per_point_protects_the_pool(test_client, admin):
    month = "2026-05"
    only = partner(test_client)
    add(clap(reader(), only["post_ids"][0], at(5, 5)))
    capped = team(test_client, admin, "put", f"/months/{month}", json={"pool_rupees": 1000, "max_per_point_rupees": 5})
    assert capped.status_code == 200 and capped.json()["max_per_point_paise"] == 500
    assert row_for(capped.json(), only["user"]["id"])["amount_paise"] == 500  # one point is worth at most ₹5
    final = team(test_client, admin, "post", f"/months/{month}/finalize", json={}).json()
    assert final["distributed_paise"] == 500


def test_only_real_finished_months_can_be_worked_on(test_client, admin):
    for bad in ("2026-13", "abc", "2099-01"):
        assert team(test_client, admin, "get", f"/months/{bad}").status_code == 404, bad
        assert team(test_client, admin, "put", f"/months/{bad}", json={"pool_rupees": 10}).status_code == 404, bad

    assert team(test_client, admin, "post", "/months/2026-06/finalize", json={}).status_code == 400  # no pool set yet
    now = current_month()
    assert team(test_client, admin, "put", f"/months/{now}", json={"pool_rupees": 500}).status_code == 200
    unfinished = team(test_client, admin, "post", f"/months/{now}/finalize", json={})
    assert unfinished.status_code == 400 and "पूरा नहीं" in unfinished.json()["detail"]

    for body in ({"pool_rupees": -1}, {"pool_rupees": 10.555}, {"pool_rupees": 5, "max_per_point_rupees": 0}):
        assert team(test_client, admin, "put", "/months/2026-06", json=body).status_code == 422, body


def test_this_month_so_far_is_shown_to_the_partner(test_client, admin):
    now = utcnow()
    db = SessionLocal()
    try:  # an earlier test may have set a pool for this month
        db.query(EarningMonth).filter(EarningMonth.month == current_month()).delete()
        db.commit()
    finally:
        db.close()
    owner = partner(test_client, approved=now - timedelta(days=10))
    add(clap(reader(), owner["post_ids"][0], now - timedelta(hours=1)))
    before = test_client.get("/api/partner/earnings", headers=owner["headers"]).json()["live"]
    assert before["month"] == current_month() and before["points"] == 1 and before["estimate_paise"] is None

    team(test_client, admin, "put", f"/months/{current_month()}", json={"pool_rupees": 500})
    after = test_client.get("/api/partner/earnings", headers=owner["headers"]).json()["live"]
    assert after["estimate_paise"] == 50_000 and after["ends_at"] > now.isoformat()


# --------------------------------------------------------------------------- paying out

def test_payouts_never_exceed_what_is_owed_and_tell_the_writer(test_client, admin):
    month = "2026-07"
    earner = partner(test_client)
    add(clap(reader(), earner["post_ids"][0], at(7, 5)))
    uid = earner["user"]["id"]
    team(test_client, admin, "put", f"/months/{month}", json={"pool_rupees": 250})
    team(test_client, admin, "post", f"/months/{month}/finalize", json={})

    due = next(row for row in team(test_client, admin, "get", "/balances").json() if row["user"]["id"] == uid)
    assert due["balance_paise"] == 25_000 and due["due"] is True and due["paid_paise"] == 0

    too_much = team(test_client, admin, "post", "/payouts", json={"user_id": uid, "amount_rupees": 300})
    assert too_much.status_code == 400 and "₹250" in too_much.json()["detail"]
    for bad in (0, -5):
        assert team(test_client, admin, "post", "/payouts", json={"user_id": uid, "amount_rupees": bad}).status_code == 422

    first = team(test_client, admin, "post", "/payouts", json={"user_id": uid, "amount_rupees": 100.5, "reference": "UPI4821"})
    assert first.status_code == 201, first.text
    assert first.json()["balance_paise"] == 14_950 and first.json()["paid_paise"] == 10_050
    assert first.json()["due"] is True  # ₹149.50 left, over the ₹100 minimum

    mine = test_client.get("/api/partner/earnings", headers=earner["headers"]).json()
    assert mine["balance_paise"] == 14_950 and [p["amount_paise"] for p in mine["payouts"]] == [10_050]
    assert mine["payouts"][0]["reference"] == "UPI4821"
    told = [n for n in test_client.get("/api/notifications", headers=earner["headers"]).json() if n["kind"] == "payout_paid"]
    assert len(told) == 1 and told[0]["text"] == "₹100.50 · UPI4821" and told[0]["actor"]["username"] == "लेखनी"

    last = team(test_client, admin, "post", "/payouts", json={"user_id": uid, "amount_rupees": 149.5})
    assert last.status_code == 201 and last.json()["balance_paise"] == 0 and last.json()["due"] is False
    empty = team(test_client, admin, "post", "/payouts", json={"user_id": uid, "amount_rupees": 1})
    assert empty.status_code == 400 and "₹0" in empty.json()["detail"]

    history = team(test_client, admin, "get", f"/payouts?user_id={uid}").json()
    assert [p["amount_paise"] for p in history] == [14_950, 10_050] and history[0]["user"]["id"] == uid
    assert "example.com" not in str(history)


def test_a_payout_needs_a_real_partner_and_never_goes_to_yourself(test_client, admin):
    stranger = _register_and_login(test_client)
    gone = team(test_client, admin, "post", "/payouts", json={"user_id": stranger["user"]["id"], "amount_rupees": 10})
    assert gone.status_code == 404
    assert team(test_client, admin, "post", "/payouts", json={"user_id": 99999999, "amount_rupees": 10}).status_code == 404

    db = SessionLocal()
    try:
        db.add(PartnerApplication(user_id=admin["user"]["id"], status=APPROVED, applied_at=at(1, 1), approved_at=at(1, 2)))
        db.commit()
    finally:
        db.close()
    own = team(test_client, admin, "post", "/payouts", json={"user_id": admin["user"]["id"], "amount_rupees": 10})
    assert own.status_code == 400 and "स्वयं" in own.json()["detail"]


def test_only_the_team_can_touch_the_money(test_client, admin):
    who = _register_and_login(test_client)
    probes = [
        ("get", "/months", None), ("get", "/months/2026-03", None),
        ("put", "/months/2026-03", {"pool_rupees": 10}), ("post", "/months/2026-03/finalize", {}),
        ("get", "/balances", None), ("get", "/payouts", None),
        ("post", "/payouts", {"user_id": who["user"]["id"], "amount_rupees": 10}),
    ]
    for method, path, body in probes:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(test_client, method)(f"/api/earnings{path}", headers=who["headers"], **kwargs).status_code == 404, path
        assert getattr(test_client, method)(f"/api/earnings{path}", **kwargs).status_code == 404, path
