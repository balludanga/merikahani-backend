"""Who the site's reviewers are: accounts whose address is in ADMIN_EMAILS *and* verified by Google.

Anyone can register with any address, so only Google vouching for it counts. Everything that
is admin-only answers everyone else with the same 404 a made-up address gets.
"""

from typing import Optional, Set

from fastapi import Depends, HTTPException

from app.core.config import settings
from app.core.dependencies import get_current_user_optional
from app.models.user import User


def admin_emails() -> Set[str]:
    return {item.strip().lower() for item in (settings.ADMIN_EMAILS or "").split(",") if item.strip()}


def is_admin(user: Optional[User]) -> bool:
    return bool(user and user.email_verified and (user.email or "").strip().lower() in admin_emails())


def require_admin(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not is_admin(user):
        raise HTTPException(status_code=404, detail="Not Found")
    return user
