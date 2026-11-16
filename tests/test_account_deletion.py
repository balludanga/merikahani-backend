"""Deleting an account: the person's content goes, the records of money stay without a name on them, the
account can never be signed into again, and it is refused where deleting would lose someone's money."""

import uuid
from unittest.mock import patch

import pytest
import requests

from app.core import account
from app.core.config import settings
from app.database import SessionLocal
from app.models.bookmark import Bookmark
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.follow import Follow
from app.models.highlight import Highlight
from app.models.notification import CLAP, Notification
from app.models.partner import APPROVED, REVOKED, PartnerApplication
from app.models.payments import ACTIVE, Membership, Payment
from app.models.post import Post
from app.models.user import User
from app.models.user_media import UserMedia
from tests.conftest import _register_and_login
from tests.test_posts import _create_post


def _delete(client, who, confirm=None):
    typed = who["user"]["username"] if confirm is None else confirm
    return client.post("/api/users/me/delete", json={"confirm_username": typed}, headers=who["headers"])


def _add(*rows):
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()
        return [getattr(row, "id", None) for row in rows]  # (a row such as UserMedia has no id)


def _one(model, **filters):
    with SessionLocal() as db:
        return db.query(model).filter_by(**filters).first()


def _count(model, *conditions):
    with SessionLocal() as db:
        return db.query(model).filter(*conditions).count()


def _story(client, who, title="रचना"):
    return _create_post(client, who["headers"], f"{title} {uuid.uuid4().hex[:6]}")["id"]


def test_everything_the_person_made_goes_and_what_others_made_stays(test_client):
    leaving, staying = _register_and_login(test_client), _register_and_login(test_client)
    uid, other = leaving["user"]["id"], staying["user"]["id"]
    mine, theirs = _story(test_client, leaving, "मेरी"), _story(test_client, staying, "उनकी")
    draft = _create_post(test_client, leaving["headers"], f"मसौदा {uuid.uuid4().hex[:6]}", published=0)["id"]
    with SessionLocal() as db:  # three taps on the other person's story, two of them from the one leaving
        db.query(Post).filter(Post.id == theirs).update({Post.clap_count: 3})
        db.commit()

    other_comment, my_comment = _add(
        Comment(content="उनकी टिप्पणी", post_id=theirs, author_id=other),
        Comment(content="मेरी टिप्पणी", post_id=theirs, author_id=uid),
    )
    reply_to_mine = _add(Comment(content="जवाब", post_id=theirs, author_id=other, parent_id=my_comment))[0]
    _add(
        Clap(user_id=uid, post_id=theirs, count=2),
        Clap(user_id=other, post_id=mine, count=1),  # someone's clap on the story that will go
        Highlight(user_id=uid, post_id=theirs, text="अंश"),
        Highlight(user_id=other, post_id=mine, text="अंश"),
        Bookmark(user_id=uid, post_id=theirs),
        Follow(follower_id=uid, following_id=other),
        Follow(follower_id=other, following_id=uid),
        Notification(user_id=other, actor_id=uid, post_id=theirs, kind=CLAP),
        Notification(user_id=uid, actor_id=other, post_id=mine, kind=CLAP),
        UserMedia(user_id=uid, avatar=b"x", avatar_type="image/png", avatar_version=1),
    )

    wrong = _delete(test_client, leaving, confirm="kisi-aur-ka")
    assert wrong.status_code == 400 and "मेल नहीं खाता" in wrong.json()["detail"]
    assert _one(User, id=uid).deleted_at is None  # nothing happened

    assert _delete(test_client, leaving).status_code == 204

    # Their stories and everything on them, their responses (and the answer under one), claps, highlights ...
    assert _count(Post, Post.author_id == uid) == 0
    assert test_client.get(f"/api/posts/{mine}").status_code == 404
    assert _count(Comment, Comment.id.in_([my_comment, reply_to_mine])) == 0
    assert _count(Clap, Clap.user_id == uid) == 0 and _count(Clap, Clap.post_id == mine) == 0
    assert _count(Highlight, Highlight.user_id == uid) == 0 and _count(Highlight, Highlight.post_id == mine) == 0
    assert _count(Bookmark, Bookmark.user_id == uid) == 0
    assert _count(Follow, (Follow.follower_id == uid) | (Follow.following_id == uid)) == 0
    assert _count(Notification, (Notification.user_id == uid) | (Notification.actor_id == uid)) == 0
    assert _count(UserMedia, UserMedia.user_id == uid) == 0
    # ... while the other person's work is untouched, and the taps came off their count.
    assert _one(Comment, id=other_comment) is not None
    assert _one(Post, id=theirs).clap_count == 1
    assert test_client.get(f"/api/posts/{theirs}").status_code == 200

    # The account is an empty shell nobody can see or sign into.
    shell = _one(User, id=uid)
    assert shell.deleted_at is not None
    assert shell.email == f"deleted-{uid}@deleted.invalid" and shell.username == f"deleted_{uid}"
    assert shell.full_name is None and shell.bio is None and shell.avatar_url is None
    assert shell.auth_provider == "deleted" and not shell.email_verified
    assert test_client.get("/api/auth/me", headers=leaving["headers"]).status_code == 401
    assert test_client.get(f"/api/users/{uid}").status_code == 404
    assert test_client.get(f"/api/prerender/user/{uid}").status_code == 404
    assert test_client.post(f"/api/follows/{uid}", headers=staying["headers"]).status_code == 404
    assert draft not in {row.id for row in _all_posts()}


def _all_posts():
    with SessionLocal() as db:
        return db.query(Post.id).all()


def test_records_of_money_stay_but_lose_what_names_a_person(test_client):
    writer, supporter = _register_and_login(test_client), _register_and_login(test_client)
    uid, supporter_id = writer["user"]["id"], supporter["user"]["id"]
    story = _story(test_client, writer)
    (application,) = _add(
        PartnerApplication(
            user_id=uid, status=APPROVED, note="मेरा संदेश", decision_note="टीम की टिप्पणी", payout_upi="mine@upi"
        )
    )
    (tip,) = _add(
        Payment(
            public_id=uuid.uuid4().hex, user_id=supporter_id, target_user_id=uid, post_id=story, purpose="tip",
            status="captured", amount_paise=5000, provider="sandbox", message="बहुत सुंदर",
        )
    )

    assert _delete(test_client, writer).status_code == 204

    kept = _one(Payment, id=tip)
    assert kept is not None and kept.amount_paise == 5000 and kept.target_user_id == uid  # the record stays
    assert kept.message is None and kept.post_id is None  # but not the supporter's words, nor a story that is gone
    row = _one(PartnerApplication, id=application)
    assert row.payout_upi is None and row.note is None and row.decision_note is None
    assert row.status == REVOKED  # no longer a partner


def test_deleting_is_refused_while_money_is_owed(test_client, monkeypatch):
    writer = _register_and_login(test_client)
    story = _story(test_client, writer)
    monkeypatch.setattr(account, "balance_paise", lambda db, user_id: 12_000)
    refused = _delete(test_client, writer)
    assert refused.status_code == 409 and "बकाया" in refused.json()["detail"]
    assert _one(User, id=writer["user"]["id"]).deleted_at is None and _one(Post, id=story) is not None
    monkeypatch.setattr(account, "balance_paise", lambda db, user_id: 0)
    assert _delete(test_client, writer).status_code == 204


def test_deleting_is_refused_while_a_membership_would_renew(test_client):
    member = _register_and_login(test_client)
    (membership,) = _add(
        Membership(user_id=member["user"]["id"], plan_code="monthly", status=ACTIVE, auto_renew=True)
    )
    refused = _delete(test_client, member)
    assert refused.status_code == 409 and "सदस्यता" in refused.json()["detail"]
    with SessionLocal() as db:
        db.query(Membership).filter(Membership.id == membership).update({Membership.auto_renew: False})
        db.commit()
    assert _delete(test_client, member).status_code == 204


def test_the_owner_cannot_delete_their_own_account_here(test_client, monkeypatch):
    owner = _register_and_login(test_client)
    with SessionLocal() as db:
        db.query(User).filter(User.id == owner["user"]["id"]).update({"email_verified": True})
        db.commit()
    monkeypatch.setattr(settings, "OWNER_EMAILS", owner["user"]["email"])
    refused = _delete(test_client, owner)
    assert refused.status_code == 409 and "मालिक" in refused.json()["detail"]
    assert _one(User, id=owner["user"]["id"]).deleted_at is None


def test_deleting_needs_a_sign_in(test_client):
    assert test_client.post("/api/users/me/delete", json={"confirm_username": "x"}).status_code == 401


def test_the_same_google_address_can_start_again_as_a_new_account(test_client):
    former = _register_and_login(test_client)
    email = former["user"]["email"]
    assert _delete(test_client, former).status_code == 204

    claims = {"email": email, "name": "फिर से", "aud": "test-google-client-id", "email_verified": "true"}
    with patch("app.api.endpoints.auth.requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = claims
        again = test_client.post("/api/auth/google-login", json={"token": "mock_google_token"})
    assert again.status_code == 200
    assert again.json()["user"]["id"] != former["user"]["id"]  # a fresh account, none of the old one


class _Answer:
    def __init__(self, body=None, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_the_uploaded_pictures_are_removed_from_the_bucket(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "service-key")
    listings = [[{"name": "a.jpg"}, {"name": "b.png"}], []]
    with patch("app.core.account.requests.post", side_effect=lambda *a, **k: _Answer(listings.pop(0))) as listed, patch(
        "app.core.account.requests.delete", return_value=_Answer()
    ) as removed:
        account.remove_uploaded_files(42)
    assert listed.call_count == 2
    assert listed.call_args_list[0].kwargs["json"]["prefix"] == "media/42"
    removed.assert_called_once()
    assert removed.call_args.kwargs["json"] == {"prefixes": ["media/42/a.jpg", "media/42/b.png"]}


def test_a_storage_failure_never_breaks_the_deletion(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "service-key")
    with patch("app.core.account.requests.post", side_effect=requests.ConnectionError("down")):
        account.remove_uploaded_files(42)  # logs, does not raise
    with patch("app.core.account.requests.post", return_value=_Answer(status=500)):
        account.remove_uploaded_files(42)


def test_without_a_storage_service_there_is_nothing_to_remove(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    with patch("app.core.account.requests.post") as post:
        account.remove_uploaded_files(42)
    post.assert_not_called()
