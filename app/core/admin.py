"""Who the site's reviewers and owners are: accounts whose address is in ADMIN_EMAILS (or
OWNER_EMAILS) *and* verified by Google.

Anyone can register with any address, so only Google vouching for it counts. Everything that
is admin-only answers everyone else with the same 404 a made-up address gets.

Two levels. A *reviewer* decides on stories and partner applications. The *owner* can do all of
that and is the only one who sees the money: the monthly sheet, the books and the payment log. A
reviewer who is not an owner gets the same 404 there, so the pages do not reveal that they exist.
"""

from typing import Optional, Set

from fastapi import Depends, HTTPException

from app.core.config import settings
from app.core.dependencies import get_current_user_optional
from app.models.user import User


def _emails(raw: Optional[str]) -> Set[str]:
    return {item.strip().lower() for item in (raw or "").split(",") if item.strip()}


def owner_emails() -> Set[str]:
    return _emails(settings.OWNER_EMAILS)


def admin_emails() -> Set[str]:
    # An owner is always a reviewer too, so the two lists never need to repeat each other.
    return _emails(settings.ADMIN_EMAILS) | owner_emails()


def _verified_in(user: Optional[User], emails: Set[str]) -> bool:
    return bool(user and user.email_verified and (user.email or "").strip().lower() in emails)


def is_admin(user: Optional[User]) -> bool:
    return _verified_in(user, admin_emails())


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
