from fastapi import APIRouter, Depends, HTTPException, Response, status, Request
from typing import Optional

from pydantic import BaseModel, Field, model_validator
import re
import requests
import logging
import secrets
from app.schemas.user import Token, UserResponse, UserCreate, UserLogin
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import timedelta
from app.core.clock import epoch_seconds, from_timestamp, utcnow
from app.database import get_db
from app.models.session import RevokedSession
from app.models.user import User
from app.core.security import DUMMY_HASH, get_password_hash, create_access_token, decode_access_token, verify_password
from app.core.config import settings
from app.core.dependencies import get_current_user, oauth2_scheme
from app.core.ratelimit import (
    EDGE_LIMIT_FACTOR, LOGIN_FAILURE_LIMIT, LOGIN_IP_LIMIT, LOGIN_WINDOW, REGISTER_IP_LIMIT, REGISTER_WINDOW,
    client_ip, edge_ip, enforce, ip_attempts, login_failures, registrations,
)

logger = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleLoginRequest(BaseModel):
    token: Optional[str] = Field(None, min_length=1, max_length=4096)         # ID token (One Tap / button)
    access_token: Optional[str] = Field(None, min_length=1, max_length=4096)  # pop-up sign-in (works on phones)

    @model_validator(mode="after")
    def exactly_one(self):
        if bool(self.token) == bool(self.access_token):
            raise ValueError("token या access_token में से ठीक एक भेजें")
        return self


def _google_get(url: str, **kwargs):
    try:
        return requests.get(url, timeout=10, **kwargs)
    except requests.RequestException as e:
        logger.error(f"Google verification unreachable: {e}")
        raise HTTPException(status_code=502, detail="गूगल (Google) से संपर्क नहीं हो सका, कृपया दोबारा कोशिश करें")


def _invalid_google_token() -> HTTPException:
    return HTTPException(status_code=401, detail="गूगल (Google) टोकन मान्य नहीं है")


def _claims_from_id_token(token: str) -> dict:
    resp = _google_get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": token})
    if resp.status_code != 200:
        logger.warning(f"Google ID-token verification failed: {resp.status_code}")
        raise _invalid_google_token()
    claims = resp.json()
    if claims.get("aud") != settings.GOOGLE_CLIENT_ID:
        logger.warning(f"Google token audience mismatch: got {claims.get('aud')}")
        raise _invalid_google_token()
    # `iss` is only present on real Google tokens; when it is there it must be Google.
    if claims.get("iss") is not None and claims.get("iss") not in GOOGLE_ISSUERS:
        raise _invalid_google_token()
    return claims


def _claims_from_access_token(access_token: str) -> dict:
    """Pop-up sign-in hands the page an access token, not an ID token. Ask Google who it
    belongs to, and refuse any token that was issued to a different app."""
    info_resp = _google_get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": access_token})
    if info_resp.status_code != 200:
        logger.warning(f"Google access-token verification failed: {info_resp.status_code}")
        raise _invalid_google_token()
    info = info_resp.json()
    if info.get("aud") != settings.GOOGLE_CLIENT_ID:
        logger.warning(f"Google access-token audience mismatch: got {info.get('aud')}")
        raise _invalid_google_token()
    profile_resp = _google_get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if profile_resp.status_code != 200:
        raise _invalid_google_token()
    profile = profile_resp.json()
    if info.get("sub") and profile.get("sub") and info["sub"] != profile["sub"]:
        raise _invalid_google_token()
    return {
        "email": profile.get("email") or info.get("email"),
        "email_verified": profile.get("email_verified", info.get("email_verified")),
        "name": profile.get("name"),
        "picture": profile.get("picture"),
    }


def _unique_username(db: Session, base: str) -> str:
    """Google sign-up derives the username from the email; never collide with a taken one."""
    # An address can hold anything in its local part ("a.b+c@gmail", "जय"). Cut it down to
    # the shape the profile allows (^[\w.]{3,30}$), or the account is born with a name its
    # own writer would be refused the day they edit their profile.
    cleaned = re.sub(r"[^\w.]", "", base or "", flags=re.UNICODE).strip("._")[:30]
    if len(cleaned) < 3:
        cleaned = f"writer{cleaned}"[:30]
    candidate = cleaned
    while db.query(User.id).filter(User.username == candidate).first():
        # 23 + "_" + 6 hex digits: the suffix has to fit inside the same 30 characters.
        candidate = f"{cleaned[:23]}_{secrets.token_hex(3)}"
    return candidate


DAY = 24 * 60 * 60


def _token_response(user: User, signed_in_at: Optional[int] = None, session_id: Optional[str] = None) -> dict:
    """A sign-in token that lasts SESSION_IDLE_DAYS and carries three extra claims: ``sid``, a random id
    for this sign-in (one per device; kept as the token is renewed, and what "sign out" revokes), ``ia``,
    when the person really signed in (Unix seconds, kept as well), and ``ra``, when this token was issued
    (the app reads it to know when a renewal is due). The token never outlives ``ia`` + SESSION_MAX_DAYS,
    so renewing cannot keep a stolen token alive for ever."""
    now = int(epoch_seconds())
    started = now if signed_in_at is None else min(signed_in_at, now)
    expires = min(now + settings.SESSION_IDLE_DAYS * DAY, started + settings.SESSION_MAX_DAYS * DAY)
    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "tv": user.token_version or 0,
            "sid": session_id or secrets.token_urlsafe(16),
            "ia": started,
            "ra": now,
        },
        expires_delta=timedelta(seconds=max(expires - now, 0)),
    )
    return {"access_token": access_token, "token_type": "bearer", "user": user}


# Plain `def` (not async): the Google check is a blocking HTTP call, which
# FastAPI runs in a worker thread instead of stalling the event loop.
@router.post("/google-login", response_model=Token)
def google_login(payload: GoogleLoginRequest, request: Request, db: Session = Depends(get_db)):
    enforce(ip_attempts, f"google:{client_ip(request)}", LOGIN_IP_LIMIT, LOGIN_WINDOW, record=True)
    enforce(ip_attempts, f"google-edge:{edge_ip(request)}", LOGIN_IP_LIMIT * EDGE_LIMIT_FACTOR, LOGIN_WINDOW, record=True)

    claims = _claims_from_access_token(payload.access_token) if payload.access_token else _claims_from_id_token(payload.token)
    # An unverified address must never claim (or create) an account for that email.
    if str(claims.get("email_verified", "")).lower() != "true":
        logger.warning("Google login rejected: email not verified")
        raise HTTPException(status_code=401, detail="गूगल (Google) खाते का ईमेल सत्यापित नहीं है")

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="गूगल (Google) खाते में ईमेल नहीं मिला")

    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user:
        now = utcnow()
        user = User(
            email=email,
            username=_unique_username(db, email.split("@")[0]),
            full_name=claims.get("name"),
            avatar_url=claims.get("picture"),
            auth_provider="google",
            email_verified=True,
            # Required column, but nobody knows this value: Google users never log in by password.
            hashed_password=get_password_hash(secrets.token_urlsafe(48)),
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # Another request created the same account (or username) a moment ago.
            db.rollback()
            user = db.query(User).filter(func.lower(User.email) == email).first()
            if user is None:
                raise HTTPException(status_code=409, detail="खाता बनाते समय दिक्कत आई, कृपया दोबारा कोशिश करें")
        else:
            db.refresh(user)
            logger.info(f"New user created: id={user.id}")
    elif user.auth_provider != "google":
        # Google has just proved this person owns the address. A password account for it
        # may have been registered by someone else (sign-up sends no verification mail),
        # so retire that password and every session issued with it, and make the account
        # Google-only. The real owner keeps their stories and signs in with Google.
        user.hashed_password = get_password_hash(secrets.token_urlsafe(48))
        user.auth_provider = "google"
        user.email_verified = True
        user.token_version = (user.token_version or 0) + 1
        user.avatar_url = user.avatar_url or claims.get("picture")
        user.full_name = user.full_name or claims.get("name")
        db.commit()
        db.refresh(user)
        logger.info(f"Password account id={user.id} converted to Google sign-in")
    return _token_response(user)


def _password_sign_in_allowed() -> bool:
    return settings.PASSWORD_SIGN_IN if settings.PASSWORD_SIGN_IN is not None else settings.debug


def _require_password_sign_in() -> None:
    """Sign-in is by Google only (the address is verified by Google and nobody can fill the site with
    made-up accounts). Anyone who made an account with a password signs in with the Google account that
    has the same address: google_login then keeps their stories and retires the password."""
    if not _password_sign_in_allowed():
        raise HTTPException(status_code=403, detail="अब साइन इन केवल गूगल (Google) से होता है")


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, request: Request, db: Session = Depends(get_db)):
    _require_password_sign_in()
    enforce(registrations, f"ip:{client_ip(request)}", REGISTER_IP_LIMIT, REGISTER_WINDOW, record=True)
    enforce(registrations, f"edge:{edge_ip(request)}", REGISTER_IP_LIMIT * EDGE_LIMIT_FACTOR, REGISTER_WINDOW, record=True)
    email = user_data.email.strip().lower()
    if db.query(User.id).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=400, detail="यह ईमेल पहले से पंजीकृत है")
    if db.query(User.id).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="यह उपयोगकर्ता नाम पहले से लिया जा चुका है")
    user = User(
        email=email,
        username=user_data.username,
        full_name=user_data.full_name,
        hashed_password=get_password_hash(user_data.password),
        auth_provider="password",
        email_verified=False,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="यह ईमेल या उपयोगकर्ता नाम पहले से लिया जा चुका है")
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(user_data: UserLogin, request: Request, db: Session = Depends(get_db)):
    _require_password_sign_in()
    enforce(ip_attempts, f"login:{client_ip(request)}", LOGIN_IP_LIMIT, LOGIN_WINDOW, record=True)
    enforce(ip_attempts, f"login-edge:{edge_ip(request)}", LOGIN_IP_LIMIT * EDGE_LIMIT_FACTOR, LOGIN_WINDOW, record=True)
    email = user_data.email.strip().lower()
    failure_key = f"email:{email}"
    enforce(login_failures, failure_key, LOGIN_FAILURE_LIMIT, LOGIN_WINDOW, record=False)
    user = db.query(User).filter(func.lower(User.email) == email).first()
    # Always run one bcrypt check so unknown emails take as long as known ones.
    password_ok = verify_password(user_data.password, user.hashed_password if user else DUMMY_HASH)
    if user and user.auth_provider == "google":
        # Google accounts have no password a human knows; point them at the right button.
        login_failures.record(failure_key)
        raise HTTPException(status_code=401, detail="इस खाते में गूगल (Google) से साइन इन करें")
    if not user or not password_ok:
        login_failures.record(failure_key)
        raise HTTPException(status_code=401, detail="ईमेल या पासवर्ड गलत है")
    login_failures.reset(failure_key)
    return _token_response(user)


@router.post("/refresh", response_model=Token)
def refresh(
    request: Request,
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
):
    """Swap a still-valid sign-in token for a fresh one, so someone who keeps coming back stays signed in.

    The old token has to be valid (not expired, not signed out, not retired by a takeover): an expired
    one cannot be revived. The new token keeps the original sign-in time, which bounds the whole chain
    to SESSION_MAX_DAYS.
    """
    enforce(ip_attempts, f"refresh:{client_ip(request)}", LOGIN_IP_LIMIT, LOGIN_WINDOW, record=True)
    enforce(ip_attempts, f"refresh-edge:{edge_ip(request)}", LOGIN_IP_LIMIT * EDGE_LIMIT_FACTOR, LOGIN_WINDOW, record=True)
    claims = decode_access_token(token)  # get_current_user has just proved it valid
    started = claims.get("ia")
    # Tokens issued before renewal existed carry no "ia": they are at most a few minutes old, so now will do.
    started = started if isinstance(started, int) else None
    if started is not None and epoch_seconds() >= started + settings.SESSION_MAX_DAYS * DAY:
        raise HTTPException(
            status_code=401,
            detail="सत्र समाप्त हो गया है, कृपया दोबारा साइन इन करें",
            headers={"WWW-Authenticate": "Bearer"},
        )
    session_id = claims.get("sid")
    return _token_response(
        current_user, signed_in_at=started, session_id=session_id if isinstance(session_id, str) else None
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sign out for real, on this device only: this sign-in (and every renewal of it) stops working, while
    the same account stays signed in on other devices.

    Sign-ins now last weeks, so deleting the token from the browser is not enough: a copy that leaked
    would stay good. Each sign-in has its own id, and the id is what gets revoked.
    """
    claims = decode_access_token(token)  # get_current_user has just proved it valid
    session_id = claims.get("sid")
    if isinstance(session_id, str) and session_id:
        started = claims.get("ia")
        started = started if isinstance(started, int) else int(epoch_seconds())
        # The id has to stay refused for as long as any renewal of this sign-in could still be alive.
        db.query(RevokedSession).filter(RevokedSession.expires_at < utcnow()).delete(synchronize_session=False)
        db.add(RevokedSession(sid=session_id, expires_at=from_timestamp(started + settings.SESSION_MAX_DAYS * DAY)))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # another tab signed this one out a moment ago: nothing left to do
    else:
        # A token from before sign-ins had ids (30 minutes at most, so only just after this was
        # deployed). It cannot be told apart from the account's other tokens: end them all.
        current_user.token_version = (current_user.token_version or 0) + 1
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    return current_user
