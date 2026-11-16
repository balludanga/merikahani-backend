"""The review team as the owner sees it."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class AddReviewer(BaseModel):
    email: EmailStr = Field(max_length=200)


class TeamMember(BaseModel):
    # The row id of a reviewer added in the app (the only kind that can be removed here); None otherwise.
    id: Optional[int] = None
    email: str
    role: str  # "owner" or "reviewer"
    source: str  # "settings" (OWNER_EMAILS / ADMIN_EMAILS) or "added" (by the owner, in the app)
    name: Optional[str] = None
    # Whether a Google-verified account exists under this address yet: until then they cannot act.
    signed_in: bool = False
    added_at: Optional[datetime] = None


class TeamOut(BaseModel):
    members: List[TeamMember]
