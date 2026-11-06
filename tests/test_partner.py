"""Write and earn: the follower line, the application, and the review team's decision."""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

from app.core.clock import utcnow
from app.database import SessionLocal
from app.models.clap import Clap
from app.models.follow import Follow
from app.models.notification import Notification
from app.models.partner import EarningLine, EarningMonth, PartnerApplication, Payout
from app.models.post import Post
from app.models.user import User
from tests.conftest import _register_and_login
from tests.test_posts import _create_post

ADMIN_EMAIL = "testuser@example.com"  # the address the mocked Google reply returns
_made_users = []  # fake followers created straight in the database, removed at the end


@pytest.fixture(autouse=True)
def _admin_is_listed():
    with patch("app.core.admin.settings.ADMIN_EMAILS", ADMIN_EMAIL):
        yield


def _wipe_programme():
    db = SessionLocal()
    try:
        for model in (Payout, EarningLine, EarningMonth, PartnerApplication):
            db.query(model).delete()
        db.query(Notification).filter(Notification.kind.like("partner_%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def _clean_slate():
    """The test database is shared by the whole run: start and finish with an empty programme."""
    _wipe_programme()
    yield
    _wipe_programme()
    db = SessionLocal()
    try:
        if _made_users:
            db.query(Follow).filter(Follow.follower_id.in_(_made_users)).delete(synchronize_session=False)
            db.query(Clap).filter(Clap.user_id.in_(_made_users)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(_made_users)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def admin(test_client):
    google = test_client.post("/api/auth/google-login", json={"token": "a" * 80})
    assert google.status_code == 200, google.text
    return {"headers": {"Authorization": f"Bearer {google.json()['access_token']}"}, "user": google.json()["user"]}


def make_followers(target_id, count, age_days=30, verified=False):
    """``count`` extra accounts that follow ``target_id`` (no passwords: they never sign in)."""
    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        users = [
            User(
                email=f"fan{tag}_{i}@example.com",
                username=f"fan{tag}_{i}",
                hashed_password="x",
                created_at=utcnow() - timedelta(days=age_days),
                email_verified=verified,
            )
            for i in range(count)
        ]
        db.add_all(users)
        db.flush()
        db.add_all([Follow(follower_id=u.id, following_id=target_id) for u in users])
        db.commit()
        ids = [u.id for u in users]
        _made_users.extend(ids)
        return ids
    finally:
        db.close()


def writer_with(test_client, followers=200, stories=1):
    who = _register_and_login(test_client)
    for number in range(stories):
        _create_post(test_client, who["headers"], f"साझेदारी की रचना {uuid.uuid4().hex[:6]} {number}")
    if followers:
        make_followers(who["user"]["id"], followers)
    return who


def my_status(test_client, who):
    response = test_client.get("/api/partner/me", headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def apply_now(test_client, who, **over):
    body = {"agree": True, "note": "मैं कहानियाँ लिखता हूँ।"}
    body.update(over)
    return test_client.post("/api/partner/apply", json=body, headers=who["headers"])


def desk(test_client, admin, path, method="get", **kwargs):
    response = getattr(test_client, method)(f"/api/partner{path}", headers=admin["headers"], **kwargs)
    return response


def application_id(test_client, admin, who, view="pending"):
    rows = test_client.get(f"/api/partner/queue?view={view}&limit=100", headers=admin["headers"]).json()
    return next(row["id"] for row in rows if row["user"]["id"] == who["user"]["id"])


def kinds(test_client, who):
    return [n["kind"] for n in test_client.get("/api/notifications", headers=who["headers"]).json()]


# --------------------------------------------------------------------------- the follower line

def test_the_follower_line_is_checked_by_the_server(test_client):
    who = writer_with(test_client, followers=199)
    status = my_status(test_client, who)
    assert status["status"] == "none"
    assert status["followers"] == {"have": 199, "need": 200, "met": False}
    assert status["stories"]["met"] is True
    assert status["can_apply"] is False and "200" in status["blocked"] and "199" in status["blocked"]

    refused = apply_now(test_client, who)
    assert refused.status_code == 400 and "200" in refused.json()["detail"]
    assert my_status(test_client, who)["status"] == "none"

    make_followers(who["user"]["id"], 1)  # the 200th follower
    status = my_status(test_client, who)
    assert status["followers"]["met"] is True and status["can_apply"] is True and status["blocked"] is None
    accepted = apply_now(test_client, who)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "pending"


def test_the_line_can_be_moved_by_configuration(test_client):
    who = writer_with(test_client, followers=3)
    assert apply_now(test_client, who).status_code == 400
    with patch("app.core.partner.settings.PARTNER_MIN_FOLLOWERS", 3):
        assert my_status(test_client, who)["followers"] == {"have": 3, "need": 3, "met": True}
        assert apply_now(test_client, who).status_code == 200


def test_a_published_story_is_needed_to_apply(test_client):
    who = writer_with(test_client, followers=200, stories=0)
    _create_post(test_client, who["headers"], "अधूरा मसौदा", published=0)  # a draft does not count
    status = my_status(test_client, who)
    assert status["stories"] == {"have": 0, "need": 1, "met": False}
    refused = apply_now(test_client, who)
    assert refused.status_code == 400 and "रचना" in refused.json()["detail"]

    _create_post(test_client, who["headers"], "प्रकाशित रचना")
    assert apply_now(test_client, who).status_code == 200


def test_agreeing_to_the_rules_is_required(test_client):
    who = writer_with(test_client)
    assert apply_now(test_client, who, agree=False).status_code == 400
    assert test_client.post("/api/partner/apply", json={}, headers=who["headers"]).status_code == 400
    assert my_status(test_client, who)["status"] == "none"


@pytest.mark.parametrize(
    "method,path", [("get", "/me"), ("post", "/apply"), ("delete", "/apply"), ("get", "/earnings"), ("put", "/payout-account")]
)
def test_the_programme_needs_a_signed_in_writer(test_client, method, path):
    kwargs = {"json": {"agree": True}} if method in ("post", "put") else {}
    assert getattr(test_client, method)(f"/api/partner{path}", **kwargs).status_code == 401


# --------------------------------------------------------------------------- applying and withdrawing

def test_apply_withdraw_and_apply_again(test_client):
    who = writer_with(test_client)
    first = apply_now(test_client, who)
    assert first.status_code == 200 and first.json()["application"]["attempts"] == 1
    again = apply_now(test_client, who)
    assert again.status_code == 400 and "समीक्षा में" in again.json()["detail"]

    withdrawn = test_client.delete("/api/partner/apply", headers=who["headers"])
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
    assert withdrawn.json()["can_apply"] is True
    assert test_client.delete("/api/partner/apply", headers=who["headers"]).status_code == 400  # nothing waiting

    second = apply_now(test_client, who, note="फिर से आवेदन")
    assert second.status_code == 200
    assert second.json()["status"] == "pending"
    assert second.json()["application"]["attempts"] == 2 and second.json()["application"]["note"] == "फिर से आवेदन"


def test_applications_are_rate_limited(test_client):
    who = writer_with(test_client)
    for _ in range(5):
        assert apply_now(test_client, who).status_code == 200
        assert test_client.delete("/api/partner/apply", headers=who["headers"]).status_code == 200
    assert apply_now(test_client, who).status_code == 429


# --------------------------------------------------------------------------- the review team's desk

def test_only_a_verified_reviewer_can_open_the_desk(test_client, admin):
    who = writer_with(test_client)
    assert apply_now(test_client, who).status_code == 200
    ident = application_id(test_client, admin, who)

    probes = [
        ("get", "/queue"), ("get", "/queue/counts"), ("get", f"/applications/{ident}"),
        ("post", f"/applications/{ident}/approve"),
        ("post", f"/applications/{ident}/decline"), ("post", f"/applications/{ident}/revoke"),
    ]
    for method, path in probes:
        kwargs = {"json": {"reason": "कोई कारण"}} if "decline" in path or "revoke" in path else {}
        assert getattr(test_client, method)(f"/api/partner{path}", headers=who["headers"], **kwargs).status_code == 404, path
        assert getattr(test_client, method)(f"/api/partner{path}", **kwargs).status_code == 404, path
    assert my_status(test_client, who)["status"] == "pending"  # untouched by the attempts above


def test_reviewers_are_told_when_someone_applies(test_client, admin):
    who = writer_with(test_client)
    assert apply_now(test_client, who, note="मेरी रचनाएँ पढ़ें").status_code == 200
    told = [
        n for n in test_client.get("/api/notifications", headers=admin["headers"]).json()
        if n["kind"] == "partner_applied" and n["actor"]["id"] == who["user"]["id"]
    ]
    assert len(told) == 1
    assert told[0]["post"] is None and told[0]["text"] == "मेरी रचनाएँ पढ़ें"
    assert "email" not in told[0]["actor"]


def test_approval_makes_a_partner_and_the_team_tells_them(test_client, admin):
    who = writer_with(test_client)
    assert apply_now(test_client, who).status_code == 200
    ident = application_id(test_client, admin, who)

    approved = desk(test_client, admin, f"/applications/{ident}/approve", "post")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["approved_at"]

    mine = my_status(test_client, who)
    assert mine["status"] == "approved" and mine["application"]["approved_at"]
    assert mine["can_apply"] is False and "साझेदार" in mine["blocked"]
    assert apply_now(test_client, who).status_code == 400

    told = [n for n in test_client.get("/api/notifications", headers=who["headers"]).json() if n["kind"] == "partner_approved"]
    assert len(told) == 1 and told[0]["post"] is None
    assert told[0]["actor"]["username"] == "समीक्षा टीम"  # the team, not the reviewer's account
    leaked = test_client.get("/api/partner/me", headers=who["headers"]).text + str(told)
    assert admin["user"]["username"] not in leaked and "decided_by" not in leaked


def test_declining_needs_a_reason_and_sets_a_wait(test_client, admin):
    who = writer_with(test_client)
    assert apply_now(test_client, who).status_code == 200
    ident = application_id(test_client, admin, who)

    assert desk(test_client, admin, f"/applications/{ident}/decline", "post", json={"reason": "  "}).status_code == 422
    assert desk(test_client, admin, f"/applications/{ident}/decline", "post", json={"reason": "कम"}).status_code == 422
    assert my_status(test_client, who)["status"] == "pending"

    reason = "रचनाओं में मौलिकता नहीं दिखी; कुछ अंश कहीं और से लिए लगते हैं।"
    declined = desk(test_client, admin, f"/applications/{ident}/decline", "post", json={"reason": reason})
    assert declined.status_code == 200 and declined.json()["status"] == "declined"

    mine = my_status(test_client, who)
    assert mine["status"] == "declined" and mine["application"]["decision_note"] == reason
    assert mine["can_apply"] is False and "दिन" in mine["blocked"]
    wait = mine["reapply_on"]
    assert wait is not None
    refused = apply_now(test_client, who)
    assert refused.status_code == 400 and "दिन बाद" in refused.json()["detail"]

    told = [n for n in test_client.get("/api/notifications", headers=who["headers"]).json() if n["kind"] == "partner_declined"]
    assert len(told) == 1 and told[0]["text"] == reason and told[0]["actor"]["username"] == "समीक्षा टीम"

    # Thirty-one days later they may try again, and the old verdict is cleared from the new application.
    db = SessionLocal()
    try:
        row = db.query(PartnerApplication).filter(PartnerApplication.user_id == who["user"]["id"]).one()
        row.decided_at = utcnow() - timedelta(days=31)
        db.commit()
    finally:
        db.close()
    assert my_status(test_client, who)["can_apply"] is True
    again = apply_now(test_client, who)
    assert again.status_code == 200
    assert again.json()["status"] == "pending" and again.json()["application"]["decision_note"] is None
    assert again.json()["application"]["attempts"] == 2


def test_the_wait_after_a_decline_follows_the_setting(test_client, admin):
    who = writer_with(test_client)
    assert apply_now(test_client, who).status_code == 200
    ident = application_id(test_client, admin, who)
    desk(test_client, admin, f"/applications/{ident}/decline", "post", json={"reason": "अभी नहीं, बाद में आइए।"})
    with patch("app.core.partner.settings.PARTNER_REAPPLY_DAYS", 0):
        assert my_status(test_client, who)["can_apply"] is True


def test_a_reviewer_cannot_decide_on_their_own_application(test_client, admin):
    make_followers(admin["user"]["id"], 200)
    _create_post(test_client, admin["headers"], f"समीक्षक की रचना {uuid.uuid4().hex[:6]}")
    assert apply_now(test_client, admin).status_code == 200
    ident = application_id(test_client, admin, admin)

    approve = desk(test_client, admin, f"/applications/{ident}/approve", "post")
    decline = desk(test_client, admin, f"/applications/{ident}/decline", "post", json={"reason": "अपने आप को मना करना"})
    assert approve.status_code == 400 and decline.status_code == 400
    assert my_status(test_client, admin)["status"] == "pending"
    assert test_client.delete("/api/partner/apply", headers=admin["headers"]).status_code == 200


def test_revoking_stops_a_partner_and_only_the_team_can_bring_them_back(test_client, admin):
    who = writer_with(test_client)
    apply_now(test_client, who)
    ident = application_id(test_client, admin, who)
    assert desk(test_client, admin, f"/applications/{ident}/revoke", "post", json={"reason": "सक्रिय नहीं"}).status_code == 400
    assert desk(test_client, admin, f"/applications/{ident}/approve", "post").status_code == 200
    first_start = my_status(test_client, who)["application"]["approved_at"]

    revoked = desk(test_client, admin, f"/applications/{ident}/revoke", "post", json={"reason": "नकली खातों से तालियाँ मिलीं।"})
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    mine = my_status(test_client, who)
    assert mine["status"] == "revoked" and mine["application"]["decision_note"] == "नकली खातों से तालियाँ मिलीं।"
    assert mine["can_apply"] is False and "रोकी" in mine["blocked"]
    assert apply_now(test_client, who).status_code == 400
    assert "partner_revoked" in kinds(test_client, who)
    # What they are owed stays visible to them.
    assert test_client.get("/api/partner/earnings", headers=who["headers"]).status_code == 200

    back = desk(test_client, admin, f"/applications/{ident}/approve", "post")
    assert back.status_code == 200 and back.json()["status"] == "approved"
    assert my_status(test_client, who)["application"]["approved_at"] >= first_start


def test_a_decision_only_fits_the_right_state(test_client, admin):
    who = writer_with(test_client)
    apply_now(test_client, who)
    ident = application_id(test_client, admin, who)
    desk(test_client, admin, f"/applications/{ident}/approve", "post")
    assert desk(test_client, admin, f"/applications/{ident}/approve", "post").status_code == 400
    assert desk(test_client, admin, f"/applications/{ident}/decline", "post", json={"reason": "देर हो गई"}).status_code == 400
    assert desk(test_client, admin, "/applications/999999", "get").status_code == 404

    # A withdrawn application is not the team's to approve; it is not in any queue, so read its id directly.
    other = writer_with(test_client)
    apply_now(test_client, other)
    test_client.delete("/api/partner/apply", headers=other["headers"])
    db = SessionLocal()
    try:
        withdrawn = db.query(PartnerApplication).filter(PartnerApplication.user_id == other["user"]["id"]).one().id
    finally:
        db.close()
    assert desk(test_client, admin, f"/applications/{withdrawn}/approve", "post").status_code == 400


def test_the_queue_lists_counts_and_searches_without_emails(test_client, admin):
    first = writer_with(test_client)
    second = writer_with(test_client)
    apply_now(test_client, first)
    apply_now(test_client, second)

    counts_before = desk(test_client, admin, "/queue/counts").json()
    pending = desk(test_client, admin, "/queue?view=pending&limit=100")
    assert pending.status_code == 200
    ids = [row["user"]["id"] for row in pending.json()]
    assert ids.index(first["user"]["id"]) < ids.index(second["user"]["id"])  # oldest first
    item = next(row for row in pending.json() if row["user"]["id"] == first["user"]["id"])
    assert item["followers_now"] == 200 and item["followers_at_apply"] == 200 and item["stories_now"] == 1
    assert "example.com" not in pending.text and "email" not in pending.text
    assert counts_before["pending"] >= 2

    found = desk(test_client, admin, f"/queue?view=pending&q={first['user']['username']}").json()
    assert [row["user"]["id"] for row in found] == [first["user"]["id"]]
    assert desk(test_client, admin, "/queue?view=everything").status_code == 422

    desk(test_client, admin, f"/applications/{application_id(test_client, admin, first)}/approve", "post")
    counts_after = desk(test_client, admin, "/queue/counts").json()
    assert counts_after["pending"] == counts_before["pending"] - 1 and counts_after["approved"] == counts_before["approved"] + 1


def test_reviewers_see_how_real_the_followers_look(test_client, admin):
    who = writer_with(test_client, followers=0, stories=0)
    post = _create_post(test_client, who["headers"], f"संकेतों की रचना {uuid.uuid4().hex[:6]}")
    uid = who["user"]["id"]
    old = make_followers(uid, 100, age_days=60)
    fresh = make_followers(uid, 60, age_days=1)
    google = make_followers(uid, 40, age_days=60, verified=True)
    assert len(old) + len(fresh) + len(google) == 200

    db = SessionLocal()
    try:
        # Ten followers have done something on the site; five are followed back; thirty joined 20 days ago.
        db.add_all([Clap(user_id=follower, post_id=post["id"], count=1) for follower in old[:10]])
        db.add_all([Follow(follower_id=uid, following_id=follower) for follower in old[10:15]])
        db.query(Post).filter(Post.id == post["id"]).update({Post.clap_count: 7})
        for follower in old[:30]:
            db.query(Follow).filter(Follow.follower_id == follower, Follow.following_id == uid).update(
                {Follow.created_at: utcnow() - timedelta(days=20)}
            )
        db.commit()
    finally:
        db.close()

    assert apply_now(test_client, who).status_code == 200
    detail = desk(test_client, admin, f"/applications/{application_id(test_client, admin, who)}").json()
    signals = detail["signals"]
    assert signals["followers"] == 200
    assert signals["followers_verified"] == 40
    assert signals["followers_new"] == 60
    assert signals["followers_inactive"] == 190
    assert signals["followers_mutual"] == 5
    assert signals["followers_week"] == 170 and signals["followers_month"] == 200
    assert signals["stories"] == 1 and signals["claps_total"] == 7
    assert detail["top_stories"][0]["slug"] == post["slug"]
    assert detail["note"] and "email" not in detail and "example.com" not in str(detail)


# --------------------------------------------------------------------------- "you can apply now"

def test_the_200th_follower_triggers_one_note(test_client):
    who = writer_with(test_client, followers=198)
    fans = [_register_and_login(test_client) for _ in range(3)]
    _made_users.extend(f["user"]["id"] for f in fans)
    assert "partner_eligible" not in kinds(test_client, who)

    assert test_client.post(f"/api/follows/{who['user']['id']}", headers=fans[0]["headers"]).status_code == 201  # 199
    assert "partner_eligible" not in kinds(test_client, who)
    assert test_client.post(f"/api/follows/{who['user']['id']}", headers=fans[1]["headers"]).status_code == 201  # 200
    assert kinds(test_client, who).count("partner_eligible") == 1
    note = next(n for n in test_client.get("/api/notifications", headers=who["headers"]).json() if n["kind"] == "partner_eligible")
    assert note["post"] is None and note["actor"]["username"] == "लेखनी"

    # More followers, or the same one toggling, never repeat it.
    assert test_client.post(f"/api/follows/{who['user']['id']}", headers=fans[2]["headers"]).status_code == 201
    test_client.delete(f"/api/follows/{who['user']['id']}", headers=fans[1]["headers"])
    test_client.post(f"/api/follows/{who['user']['id']}", headers=fans[1]["headers"])
    assert kinds(test_client, who).count("partner_eligible") == 1


def test_a_writer_who_already_applied_is_not_nudged(test_client):
    who = writer_with(test_client, followers=200)
    apply_now(test_client, who)
    fan = _register_and_login(test_client)
    _made_users.append(fan["user"]["id"])
    assert test_client.post(f"/api/follows/{who['user']['id']}", headers=fan["headers"]).status_code == 201
    assert "partner_eligible" not in kinds(test_client, who)


# --------------------------------------------------------------------------- the payout address

def test_the_payout_address_is_private_and_checked(test_client, admin):
    who = writer_with(test_client)
    upi = {"upi_id": "kavi.lekhak@okaxis"}
    assert test_client.put("/api/partner/payout-account", json=upi, headers=who["headers"]).status_code == 403  # not a partner yet

    apply_now(test_client, who)
    desk(test_client, admin, f"/applications/{application_id(test_client, admin, who)}/approve", "post")
    saved = test_client.put("/api/partner/payout-account", json=upi, headers=who["headers"])
    assert saved.status_code == 200 and saved.json()["payout_upi"] == "kavi.lekhak@okaxis"
    assert my_status(test_client, who)["payout_upi"] == "kavi.lekhak@okaxis"

    for bad in ("kavi lekhak@okaxis", "kavi@", "@bank", "kavi.lekhak", "a@b", "<script>@x.y"):
        assert test_client.put("/api/partner/payout-account", json={"upi_id": bad}, headers=who["headers"]).status_code == 422, bad

    # Nobody reads it but the writer and the review team.
    uid = who["user"]["id"]
    assert "kavi.lekhak" not in test_client.get(f"/api/users/{uid}").text
    assert "kavi.lekhak" not in test_client.get(f"/api/users/{uid}", headers=who["headers"]).text
    assert "kavi.lekhak" not in test_client.get("/api/notifications", headers=who["headers"]).text
    other = writer_with(test_client, followers=0, stories=0)
    assert "kavi.lekhak" not in test_client.get("/api/partner/me", headers=other["headers"]).text
    assert desk(test_client, admin, f"/applications/{application_id(test_client, admin, who, 'approved')}").json()["payout_upi"] == "kavi.lekhak@okaxis"

    cleared = test_client.put("/api/partner/payout-account", json={"upi_id": "  "}, headers=who["headers"])
    assert cleared.status_code == 200 and cleared.json()["payout_upi"] is None


def test_the_earnings_page_is_for_partners(test_client, admin):
    who = writer_with(test_client)
    assert test_client.get("/api/partner/earnings", headers=who["headers"]).status_code == 403
    apply_now(test_client, who)
    assert test_client.get("/api/partner/earnings", headers=who["headers"]).status_code == 403  # waiting is not enough
    desk(test_client, admin, f"/applications/{application_id(test_client, admin, who)}/approve", "post")
    page = test_client.get("/api/partner/earnings", headers=who["headers"])
    assert page.status_code == 200
    body = page.json()
    assert body["balance_paise"] == 0 and body["months"] == [] and body["payouts"] == []
    assert body["live"]["points"] == 0 and body["live"]["estimate_paise"] is None


def test_the_rules_the_page_quotes_come_from_the_server(test_client):
    who = writer_with(test_client, followers=0, stories=0)
    rules = my_status(test_client, who)["rules"]
    public = test_client.get("/api/partner/rules")  # a visitor who is not signed in may read them too
    assert public.status_code == 200 and public.json() == rules
    assert rules["min_followers"] == 200 and rules["min_stories"] == 1
    assert rules["clap_points"] == 1 and rules["highlight_points"] == 2 and rules["max_highlights"] == 3
    assert rules["response_points"] == 5 and rules["max_responses"] == 2
    assert rules["reader_min_age_days"] == 7 and rules["reapply_days"] == 30 and rules["min_payout_paise"] == 10000
