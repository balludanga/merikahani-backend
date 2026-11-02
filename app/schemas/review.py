from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.review import CATEGORIES


def _clean(value: Optional[str], limit: int) -> Optional[str]:
    cleaned = (value or "").strip()
    return cleaned[:limit] if cleaned else None


class NoteCreate(BaseModel):
    text: str
    start: Optional[int] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    category: str = "other"
    comment: str
    suggestion: Optional[str] = None

    @field_validator("text")
    @classmethod
    def passage(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("चुना हुआ अंश कम से कम 2 अक्षर का होना चाहिए")
        return cleaned[:2000]

    @field_validator("start")
    @classmethod
    def start_in_range(cls, value: Optional[int]) -> Optional[int]:
        return value if value is not None and 0 <= value < 10_000_000 else None

    @field_validator("prefix", "suffix")
    @classmethod
    def cap_context(cls, value: Optional[str]) -> Optional[str]:
        return value[:80] if value else None

    @field_validator("category")
    @classmethod
    def known_category(cls, value: str) -> str:
        return value if value in CATEGORIES else "other"

    @field_validator("comment")
    @classmethod
    def comment_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("टिप्पणी खाली नहीं हो सकती")
        return cleaned[:1000]

    @field_validator("suggestion")
    @classmethod
    def suggestion_cap(cls, value: Optional[str]) -> Optional[str]:
        return _clean(value, 2000)


class NoteUpdate(BaseModel):
    """Partial edit; an empty suggestion removes it."""

    category: Optional[str] = None
    comment: Optional[str] = None
    suggestion: Optional[str] = None

    @field_validator("category")
    @classmethod
    def known_category(cls, value: Optional[str]) -> Optional[str]:
        return value if value is None or value in CATEGORIES else "other"

    @field_validator("comment")
    @classmethod
    def comment_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("टिप्पणी खाली नहीं हो सकती")
        return cleaned[:1000]

    @field_validator("suggestion")
    @classmethod
    def suggestion_cap(cls, value: Optional[str]) -> Optional[str]:
        return (value or "").strip()[:2000]


class NoteStatus(BaseModel):
    status: Literal["open", "fixed"]


class RequestChanges(BaseModel):
    summary: Optional[str] = None

    @field_validator("summary")
    @classmethod
    def summary_cap(cls, value: Optional[str]) -> Optional[str]:
        return _clean(value, 1000)


class NoteResponse(BaseModel):
    id: int
    post_id: int
    round_id: Optional[int] = None
    text: str
    start_offset: Optional[int] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    category: str
    comment: str
    suggestion: Optional[str] = None
    status: str
    created_at: datetime
    # Not yet sent to the writer: only reviewers see it.
    draft: bool = False

    model_config = ConfigDict(from_attributes=True)


class RoundResponse(BaseModel):
    id: int
    summary: Optional[str] = None
    created_at: datetime
    approved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ReviewPost(BaseModel):
    id: int
    title: str
    slug: str
    published: int
    moderation_state: str
    author_id: int

    model_config = ConfigDict(from_attributes=True)


class ReviewState(BaseModel):
    post: ReviewPost
    # True for a reviewer, False for the story's own writer.
    reviewer: bool
    rounds: List[RoundResponse]
    notes: List[NoteResponse]


class QueueAuthor(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class QueueItem(BaseModel):
    id: int
    title: str
    slug: str
    published: int
    moderation_state: str
    created_at: datetime
    updated_at: datetime
    author: QueueAuthor
    open_notes: int = 0
    fixed_notes: int = 0
    draft_notes: int = 0
    summary: Optional[str] = None


class QueueCounts(BaseModel):
    resubmitted: int
    changes_requested: int
