"""The owner builds the review team from the app: add a Google address, remove it again, and nobody
else can. A reviewer added this way can review; money stays the owner's."""

from unittest.mock import patch

import pytest

from app.core.admin import admin_emails, is_admin
from app.core.review import reviewers
from app.database import SessionLocal
from app.models.reviewer import Reviewer
from app.models.user import User
from tests.conftest import _register_and_login


def verified_account(client, verified=True):
    who = _register_and_login(client)
    with SessionLocal() as db:
        db.query(User).filter(User.id == who["user"]["id"]).update({"email_verified": verified})
        db.commit()
    return who


@pytest.fixture(scope="module")
def owner(test_client):
    return verified_account(test_client)


@pytest.fixture(autouse=True)
def _only_the_owner_is_listed(owner):
    """The settings list one owner and no reviewer; every other reviewer in these tests is added in the app."""
    with patch("app.core.admin.settings.OWNER_EMAILS", owner["user"]["email"]), patch(
        "app.core.admin.settings.ADMIN_EMAILS", ""
    ):
        yield
    with SessionLocal() as db:
        db.query(Reviewer).delete()
        db.commit()


def add(client, who, email):
    return client.post("/api/team/reviewers", json={"email": email}, headers=who["headers"])


def remove(client, who, reviewer_id):
    return client.delete(f"/api/team/reviewers/{reviewer_id}", headers=who["headers"])


def team(client, who):
    return client.get("/api/team/reviewers", headers=who["headers"])


def can_review(client, who):
    return client.get("/api/reviews/queue/counts", headers=who["headers"]).status_code == 200


def acts_on_money(client, who):
    return client.put("/api/earnings/months/2026-05", json={"pool_rupees": 1}, headers=who["headers"]).status_code != 404


def test_a_reviewer_the_owner_adds_can_review_and_nothing_more(test_client, owner):
    colleague = verified_account(test_client)
    assert not can_review(test_client, colleague)  # nobody yet

    added = add(test_client, owner, colleague["user"]["email"])
    assert added.status_code == 201
    mine = [m for m in added.json()["members"] if m["email"] == colleague["user"]["email"]]
    assert len(mine) == 1 and mine[0]["role"] == "reviewer" and mine[0]["source"] == "added"
    assert mine[0]["signed_in"] is True and mine[0]["id"] is not None

    assert can_review(test_client, colleague)
    assert test_client.get("/api/auth/me", headers=colleague["headers"]).json()["is_admin"] is True
    assert test_client.get("/api/auth/me", headers=colleague["headers"]).json()["is_owner"] is False
    assert not acts_on_money(test_client, colleague)  # the money stays the owner's


def test_removing_a_reviewer_takes_effect_on_their_next_request(test_client, owner):
    colleague = verified_account(test_client)
    row_id = next(m["id"] for m in add(test_client, owner, colleague["user"]["email"]).json()["members"] if m["id"])
    assert can_review(test_client, colleague)

    assert remove(test_client, owner, row_id).status_code == 204
    assert not can_review(test_client, colleague)  # same token, no new sign-in needed
    assert test_client.get("/api/auth/me", headers=colleague["headers"]).json()["is_admin"] is False
    assert remove(test_client, owner, row_id).status_code == 404  # already gone


def test_an_address_google_has_not_verified_is_not_a_reviewer(test_client, owner):
    stranger = verified_account(test_client, verified=False)  # a password sign-up with that address
    assert add(test_client, owner, stranger["user"]["email"]).status_code == 201
    assert not can_review(test_client, stranger)
    with SessionLocal() as db:
        db.query(User).filter(User.id == stranger["user"]["id"]).update({"email_verified": True})
        db.commit()
    assert can_review(test_client, stranger)  # the moment Google vouches for it


def test_a_person_can_be_added_before_they_have_signed_in(test_client, owner):
    added = add(test_client, owner, "  Newcomer.Reviewer@Example.com ").json()
    row = next(m for m in added["members"] if m["source"] == "added")
    assert row["email"] == "newcomer.reviewer@example.com"  # tidied to lower case, spaces gone
    assert row["signed_in"] is False and row["name"] is None


def test_only_the_owner_can_see_or_change_the_team(test_client, owner):
    reviewer = verified_account(test_client)
    plain = verified_account(test_client)
    add(test_client, owner, reviewer["user"]["email"])
    row_id = next(m["id"] for m in team(test_client, owner).json()["members"] if m["id"])
    for who in (reviewer, plain):  # a reviewer is not enough, and neither is any other account
        assert team(test_client, who).status_code == 404
        assert add(test_client, who, "someone@example.com").status_code == 404
        assert remove(test_client, who, row_id).status_code == 404
    assert test_client.get("/api/team/reviewers").status_code == 404  # nobody signed in
    assert test_client.post("/api/team/reviewers", json={"email": "someone@example.com"}).status_code == 404
    assert can_review(test_client, reviewer)  # and nothing above removed anyone


def test_owners_and_the_settings_list_cannot_be_added_or_removed_here(test_client, owner):
    fixed = verified_account(test_client)
    with patch("app.core.admin.settings.ADMIN_EMAILS", fixed["user"]["email"]):
        members = team(test_client, owner).json()["members"]
        roles = {m["email"]: (m["role"], m["source"], m["id"]) for m in members}
        assert roles[owner["user"]["email"]] == ("owner", "settings", None)
        assert roles[fixed["user"]["email"]] == ("reviewer", "settings", None)
        assert add(test_client, owner, fixed["user"]["email"]).status_code == 409
    assert add(test_client, owner, owner["user"]["email"]).status_code == 409  # an owner is already on the team


def test_adding_the_same_address_twice_is_refused_and_a_bad_address_is_rejected(test_client, owner):
    assert add(test_client, owner, "twice@example.com").status_code == 201
    again = add(test_client, owner, "TWICE@example.com")
    assert again.status_code == 409 and "पहले से" in again.json()["detail"]
    with SessionLocal() as db:
        assert db.query(Reviewer).filter(Reviewer.email == "twice@example.com").count() == 1
    for bad in ("", "not-an-address", "a@", "x" * 300 + "@example.com"):
        assert add(test_client, owner, bad).status_code == 422, bad


def test_the_team_is_told_when_a_writer_sends_a_story_back(test_client, owner):
    """Resubmission notices go to every reviewer, the ones added in the app included."""
    colleague = verified_account(test_client)
    outsider = verified_account(test_client)
    add(test_client, owner, colleague["user"]["email"])
    with SessionLocal() as db:
        ids = {user.id for user in reviewers(db)}
        assert colleague["user"]["id"] in ids and outsider["user"]["id"] not in ids
        assert colleague["user"]["email"] in admin_emails(db) and colleague["user"]["email"] not in admin_emails()
        account = db.query(User).filter(User.id == colleague["user"]["id"]).one()
        assert is_admin(account)
    with SessionLocal() as db:
        detached = db.query(User).filter(User.id == colleague["user"]["id"]).one()
        db.expunge(detached)
    assert not is_admin(detached)  # an account with no session cannot be looked up, so it is simply not a reviewer
