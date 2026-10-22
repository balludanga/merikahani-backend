from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
import requests
import logging
import secrets
from app.schemas.user import Token, UserResponse, UserCreate, UserLogin
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database import get_db
from app.models.user import User
from app.core.security import DUMMY_HASH, get_password_hash, create_access_token, verify_password
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.core.ratelimit import (
    LOGIN_FAILURE_LIMIT, LOGIN_IP_LIMIT, LOGIN_WINDOW, REGISTER_IP_LIMIT, REGISTER_WINDOW,
    client_ip, enforce, ip_attempts, login_failures, registrations,
)

logger = logging.getLogger(__name__)

router = APIRouter()

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleLoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


def _unique_username(db: Session, base: str) -> str:
    """Google sign-up derives the username from the email; never collide with a taken one."""
    base = (base or "writer")[:30]
    candidate = base
    while db.query(User.id).filter(User.username == candidate).first():
        candidate = f"{base[:24]}_{secrets.token_hex(3)}"
    return candidate


def _token_response(user: User) -> dict:
    access_token = create_access_token(
        data={"sub": str(user.id), "tv": user.token_version or 0},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer", "user": user}


# Plain `def` (not async): the Google check is a blocking HTTP call, which
# FastAPI runs in a worker thread instead of stalling the event loop.
@router.post("/google-login", response_model=Token)
def google_login(payload: GoogleLoginRequest, request: Request, db: Session = Depends(get_db)):
    enforce(ip_attempts, f"google:{client_ip(request)}", LOGIN_IP_LIMIT, LOGIN_WINDOW, record=True)

    try:
        resp = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": payload.token},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error(f"Google token verification unreachable: {e}")
        raise HTTPException(status_code=502, detail="गूगल (Google) से संपर्क नहीं हो सका, कृपया दोबारा कोशिश करें")
    if resp.status_code != 200:
        logger.warning(f"Google token verification failed: {resp.status_code}")
        raise HTTPException(status_code=401, detail="गूगल (Google) टोकन मान्य नहीं है")
    claims = resp.json()
    if claims.get("aud") != settings.GOOGLE_CLIENT_ID:
        logger.warning(f"Google token audience mismatch: got {claims.get('aud')}")
        raise HTTPException(status_code=401, detail="गूगल (Google) टोकन मान्य नहीं है")
    # `iss` is only present on real Google tokens; when it is there it must be Google.
    if claims.get("iss") is not None and claims.get("iss") not in GOOGLE_ISSUERS:
        raise HTTPException(status_code=401, detail="गूगल (Google) टोकन मान्य नहीं है")
    # An unverified address must never claim (or create) an account for that email.
    if str(claims.get("email_verified", "")).lower() != "true":
        logger.warning("Google login rejected: email not verified")
        raise HTTPException(status_code=401, detail="गूगल (Google) खाते का ईमेल सत्यापित नहीं है")

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="गूगल (Google) खाते में ईमेल नहीं मिला")

    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user:
        now = datetime.utcnow()
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


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, request: Request, db: Session = Depends(get_db)):
    enforce(registrations, f"ip:{client_ip(request)}", REGISTER_IP_LIMIT, REGISTER_WINDOW, record=True)
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
    enforce(ip_attempts, f"login:{client_ip(request)}", LOGIN_IP_LIMIT, LOGIN_WINDOW, record=True)
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


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    return current_user
