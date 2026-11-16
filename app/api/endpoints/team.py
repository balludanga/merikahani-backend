"""The review team, managed by the owner.

Reviewers decide on stories and on partner applications. The owner adds and removes them here, by Google
address, instead of editing ADMIN_EMAILS on the host. Only an owner (OWNER_EMAILS, verified by Google) can
reach any of this; everyone else, a reviewer included, gets the same 404 a made-up address gets. Owners
themselves cannot be made here, and neither can the addresses fixed in the settings be removed.
"""

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.admin import admin_emails, owner_emails, require_owner
from app.core.ratelimit import actions, enforce
from app.database import get_db
from app.models.reviewer import Reviewer
from app.models.user import User
from app.schemas.team import AddReviewer, TeamMember, TeamOut

router = APIRouter()

CHANGE_LIMIT = (30, 60 * 60)  # additions per hour


def _team(db: Session) -> TeamOut:
    owners = owner_emails()
    from_settings = admin_emails() - owners
    added = db.query(Reviewer).order_by(Reviewer.created_at, Reviewer.id).all()
    everyone = sorted(owners) + sorted(from_settings) + [row.email for row in added]
    accounts: Dict[str, User] = {
        (user.email or "").lower(): user
        for user in db.query(User).filter(func.lower(User.email).in_(everyone or [""]), User.deleted_at.is_(None))
    }

    def member(email: str, role: str, source: str, row: Reviewer = None) -> TeamMember:
        account = accounts.get(email)
        return TeamMember(
            id=row.id if row is not None else None,
            email=email,
            role=role,
            source=source,
            name=(account.full_name or account.username) if account is not None else None,
            signed_in=bool(account is not None and account.email_verified),
            added_at=row.created_at if row is not None else None,
        )

    members: List[TeamMember] = [member(email, "owner", "settings") for email in sorted(owners)]
    members += [member(email, "reviewer", "settings") for email in sorted(from_settings)]
    members += [member(row.email, "reviewer", "added", row) for row in added]
    return TeamOut(members=members)


@router.get("/reviewers", response_model=TeamOut)
def list_team(db: Session = Depends(get_db), _owner: User = Depends(require_owner)):
    """The owners, the reviewers fixed in the settings, and the reviewers added in the app."""
    return _team(db)


@router.post("/reviewers", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def add_reviewer(payload: AddReviewer, db: Session = Depends(get_db), owner: User = Depends(require_owner)):
    """Make whoever signs in with Google under this address a reviewer."""
    enforce(actions, f"team:{owner.id}", *CHANGE_LIMIT, record=True)
    email = payload.email.strip().lower()
    if email in admin_emails(db):
        raise HTTPException(status_code=409, detail="यह पता पहले से टीम में है")
    db.add(Reviewer(email=email, added_by=owner.id))
    try:
        db.commit()
    except IntegrityError:  # added a moment ago from another tab
        db.rollback()
        raise HTTPException(status_code=409, detail="यह पता पहले से टीम में है")
    return _team(db)


@router.delete("/reviewers/{reviewer_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_reviewer(reviewer_id: int, db: Session = Depends(get_db), _owner: User = Depends(require_owner)):
    """Take a reviewer added in the app off the team. It takes effect on their very next request."""
    row = db.query(Reviewer).filter(Reviewer.id == reviewer_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="समीक्षक नहीं मिला")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
