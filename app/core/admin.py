"""Who the site's reviewers and owners are: accounts whose address is in ADMIN_EMAILS (or
OWNER_EMAILS) *and* verified by Google.

Anyone can register with any address, so only Google vouching for it counts. Everything that
is admin-only answers everyone else with the same 404 a made-up address gets.

A reviewer comes in two ways: the address is in ADMIN_EMAILS (set on the host), or the owner added it
from the app (the ``reviewers`` table). Both need Google to have verified the address. Owners come only
from OWNER_EMAILS: nobody can be made an owner from the app.

Two levels. A *reviewer* decides on stories and partner applications. The *owner* can do all of
that and is the only one who sees the money: the monthly sheet, the books and the payment log, and the
only one who may act on it (set a month's pool, finalize it, record a payout, list what is owed). A
reviewer who is not an owner gets the same 404 there, so the pages do not reveal that they exist.
(Reviewers may still read each month's pool and result and the payout history.)
"""

from typing import Optional, Set

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.core.dependencies import get_current_user_optional
from app.models.reviewer import Reviewer
from app.models.user import User


def _emails(raw: Optional[str]) -> Set[str]:
    return {item.strip().lower() for item in (raw or "").split(",") if item.strip()}


def owner_emails() -> Set[str]:
    return _emails(settings.OWNER_EMAILS)


def admin_emails(db: Optional[Session] = None) -> Set[str]:
    """The addresses of the review team: ADMIN_EMAILS and the owners, plus (when a session is given) the
    ones the owner added in the app. An owner is always a reviewer too, so the lists never need to repeat
    each other."""
    emails = _emails(settings.ADMIN_EMAILS) | owner_emails()
    if db is not None:
        emails |= {row.email for row in db.query(Reviewer.email)}
    return emails


def _verified_in(user: Optional[User], emails: Set[str]) -> bool:
    return bool(user and user.email_verified and (user.email or "").strip().lower() in emails)


def _added_in_app(user: User) -> bool:
    """Whether the owner added this account's address to the team from the app. Looked up through the
    session the account was loaded in; an account that is not attached to one is simply not found."""
    session = object_session(user)
    if session is None:
        return False
    email = (user.email or "").strip().lower()
    return session.query(Reviewer.id).filter(Reviewer.email == email).first() is not None


def is_admin(user: Optional[User]) -> bool:
    if not (user and user.email_verified):
        return False
    return _verified_in(user, admin_emails()) or _added_in_app(user)


def is_owner(user: Optional[User]) -> bool:
    return _verified_in(user, owner_emails())


def require_admin(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not is_admin(user):
        raise HTTPException(status_code=404, detail="Not Found")
    return user


def require_owner(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not is_owner(user):
        raise HTTPException(status_code=404, detail="Not Found")
    return user
