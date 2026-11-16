"""Staying signed in: a sign-in lasts weeks, is renewed while the person keeps coming back, has an
outer limit, and really ends when they sign out (on that device only)."""

import time
import uuid
from datetime import timedelta, timezone

import jwt as pyjwt

from app.core.clock import utcnow
from app.core.config import settings

DAY = 24 * 60 * 60


def _claims(token: str) -> dict:
    return pyjwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])


def _forge(uid: int, tv: int = 0, **claims) -> str:
    """A token signed with the real key, so each test can choose its own clock readings."""
    return pyjwt.encode({"sub": str(uid), "tv": tv, **claims}, settings.SECRET_KEY, algorithm="HS256")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_account(client) -> dict:
    """A fresh account, so signing out in one test cannot disturb the shared module fixtures."""
    unique = uuid.uuid4().hex[:8]
    body = {"email": f"sess_{unique}@example.com", "username": f"sess_{unique}", "password": "secret123"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    login = client.post("/api/auth/login", json={"email": body["email"], "password": body["password"]})
    assert login.status_code == 200, login.text
    data = login.json()
    return {"id": data["user"]["id"], "token": data["access_token"], "login": body}


def test_a_sign_in_lasts_weeks_and_records_when_it_started(test_client):
    me = _new_account(test_client)
    claims = _claims(me["token"])
    now = time.time()
    assert abs(claims["exp"] - (now + settings.SESSION_IDLE_DAYS * DAY)) < 60
    assert settings.SESSION_IDLE_DAYS >= 30
    assert abs(claims["ia"] - now) < 60 and claims["ra"] == claims["ia"]


def test_refresh_gives_a_fresh_token_and_keeps_the_original_sign_in_time(test_client):
    me = _new_account(test_client)
    now = int(time.time())
    week_old = _forge(me["id"], exp=now + 23 * DAY, ia=now - 10 * DAY, ra=now - 7 * DAY)

    response = test_client.post("/api/auth/refresh", headers=_bearer(week_old))
    assert response.status_code == 200, response.text
    body = response.json()
    renewed = _claims(body["access_token"])

    assert renewed["ia"] == now - 10 * DAY  # still counted from the real sign-in
    assert abs(renewed["ra"] - now) < 60
    assert abs(renewed["exp"] - (now + settings.SESSION_IDLE_DAYS * DAY)) < 60
    assert body["user"]["id"] == me["id"] and "email" in body["user"]
    assert test_client.get("/api/auth/me", headers=_bearer(body["access_token"])).status_code == 200


def test_a_token_from_before_renewal_existed_can_still_be_renewed(test_client):
    me = _new_account(test_client)
    legacy = _forge(me["id"], exp=int(time.time()) + 600)  # no "ia", no "ra"
    response = test_client.post("/api/auth/refresh", headers=_bearer(legacy))
    assert response.status_code == 200
    assert "ia" in _claims(response.json()["access_token"])


def test_renewing_never_stretches_past_the_outer_limit(test_client):
    me = _new_account(test_client)
    now = int(time.time())
    limit = settings.SESSION_MAX_DAYS * DAY

    # Signed in 2 days short of the limit: the renewal is cut to what is left, not given a fresh 30 days.
    started = now - limit + 2 * DAY
    near = _forge(me["id"], exp=now + 600, ia=started, ra=now - 5 * DAY)
    response = test_client.post("/api/auth/refresh", headers=_bearer(near))
    assert response.status_code == 200
    assert _claims(response.json()["access_token"])["exp"] <= started + limit

    # Past the limit: no renewal, even though this token itself has not expired yet.
    past = _forge(me["id"], exp=now + 600, ia=now - limit - 60, ra=now - DAY)
    response = test_client.post("/api/auth/refresh", headers=_bearer(past))
    assert response.status_code == 401 and "समाप्त" in response.json()["detail"]


def test_an_expired_or_missing_or_retired_token_cannot_be_renewed(test_client):
    me = _new_account(test_client)
    now = int(time.time())
    assert test_client.post("/api/auth/refresh").status_code == 401
    expired = _forge(me["id"], exp=now - 5, ia=now - DAY, ra=now - DAY)
    assert test_client.post("/api/auth/refresh", headers=_bearer(expired)).status_code == 401
    retired = _forge(me["id"], tv=99, exp=now + DAY, ia=now, ra=now)
    assert test_client.post("/api/auth/refresh", headers=_bearer(retired)).status_code == 401
    assert test_client.post("/api/auth/refresh", headers=_bearer("not.a.token")).status_code == 401


def test_refresh_is_rate_limited(test_client):
    from app.core.ratelimit import LOGIN_IP_LIMIT

    me = _new_account(test_client)
    codes = [test_client.post("/api/auth/refresh", headers=_bearer(me["token"])).status_code for _ in range(LOGIN_IP_LIMIT + 1)]
    assert codes[:LOGIN_IP_LIMIT] == [200] * LOGIN_IP_LIMIT
    assert codes[-1] == 429


def _sign_in(client, me) -> str:
    login = client.post("/api/auth/login", json={"email": me["login"]["email"], "password": me["login"]["password"]})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def test_each_sign_in_has_its_own_id_and_renewing_keeps_it(test_client):
    me = _new_account(test_client)
    phone, laptop = me["token"], _sign_in(test_client, me)
    assert _claims(phone)["sid"] != _claims(laptop)["sid"]

    renewed = test_client.post("/api/auth/refresh", headers=_bearer(phone)).json()["access_token"]
    assert _claims(renewed)["sid"] == _claims(phone)["sid"]


def test_signing_out_ends_only_this_device(test_client):
    me = _new_account(test_client)
    phone, laptop = me["token"], _sign_in(test_client, me)
    # The phone has renewed its sign-in once, so two tokens of the same sign-in exist.
    phone_renewed = test_client.post("/api/auth/refresh", headers=_bearer(phone)).json()["access_token"]

    assert test_client.post("/api/auth/logout", headers=_bearer(phone_renewed)).status_code == 204

    # Everything from the phone's sign-in is finished, the older token and its renewal alike...
    for token in (phone, phone_renewed):
        assert test_client.get("/api/auth/me", headers=_bearer(token)).status_code == 401
        assert test_client.post("/api/auth/refresh", headers=_bearer(token)).status_code == 401
    # ...while the laptop is still signed in, and can still renew its sign-in.
    assert test_client.get("/api/auth/me", headers=_bearer(laptop)).status_code == 200
    assert test_client.post("/api/auth/refresh", headers=_bearer(laptop)).status_code == 200
    # Signing in again on the phone gives a new, working sign-in.
    assert test_client.get("/api/auth/me", headers=_bearer(_sign_in(test_client, me))).status_code == 200


def test_the_ended_sign_in_is_remembered_until_it_could_no_longer_be_renewed(test_client):
    from app.database import SessionLocal
    from app.models.session import RevokedSession

    me = _new_account(test_client)
    sid = _claims(me["token"])["sid"]
    assert test_client.post("/api/auth/logout", headers=_bearer(me["token"])).status_code == 204
    # Signing out twice is harmless: the second call is refused like any other use of an ended sign-in.
    assert test_client.post("/api/auth/logout", headers=_bearer(me["token"])).status_code == 401

    db = SessionLocal()
    try:
        row = db.query(RevokedSession).filter(RevokedSession.sid == sid).one()
        started = _claims(me["token"])["ia"]
        assert abs(row.expires_at.replace(tzinfo=timezone.utc).timestamp() - (started + settings.SESSION_MAX_DAYS * DAY)) < 5

        # A row past its time is swept away by the next sign-out, and never touches a live one.
        db.add(RevokedSession(sid="long-gone-session-id", expires_at=utcnow() - timedelta(days=1)))
        db.commit()
    finally:
        db.close()
    other = _new_account(test_client)
    assert test_client.post("/api/auth/logout", headers=_bearer(other["token"])).status_code == 204
    db = SessionLocal()
    try:
        assert db.query(RevokedSession).filter(RevokedSession.sid == "long-gone-session-id").first() is None
        assert db.query(RevokedSession).filter(RevokedSession.sid == sid).first() is not None
    finally:
        db.close()


def test_signing_out_with_a_token_that_has_no_id_ends_the_account_everywhere(test_client):
    """Tokens made before sign-ins had ids (at most 30 minutes old) cannot be told apart from the
    account's others, so signing out with one ends them all: the safe way round."""
    me = _new_account(test_client)
    other_device = _sign_in(test_client, me)
    legacy = _forge(me["id"], exp=int(time.time()) + 600)  # no "sid"

    assert test_client.post("/api/auth/logout", headers=_bearer(legacy)).status_code == 204
    for token in (legacy, other_device, me["token"]):
        assert test_client.get("/api/auth/me", headers=_bearer(token)).status_code == 401
    assert test_client.get("/api/auth/me", headers=_bearer(_sign_in(test_client, me))).status_code == 200


def test_signing_out_needs_a_sign_in(test_client):
    assert test_client.post("/api/auth/logout").status_code == 401
