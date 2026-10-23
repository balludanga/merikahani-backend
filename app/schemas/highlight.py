from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime
from typing import Optional


class HighlightCreate(BaseModel):
    post_id: int
    text: str
    start: Optional[int] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("हाइलाइट का अंश कम से कम 2 अक्षर का होना चाहिए")
        return cleaned[:2000]

    @field_validator("start")
    @classmethod
    def start_not_negative(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        return value if 0 <= value < 10_000_000 else None

    @field_validator("prefix", "suffix")
    @classmethod
    def cap_context(cls, value: Optional[str]) -> Optional[str]:
        # Collapsed text as the browser sees it; edge spaces matter, so keep them.
        return value[:80] if value else None


class HighlightPost(BaseModel):
    id: int
    title: str
    slug: str

    model_config = ConfigDict(from_attributes=True)


class HighlightResponse(BaseModel):
    id: int
    post_id: int
    text: str
    start_offset: Optional[int] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    created_at: datetime
    # The story it is from; filled in for the reader's own list so it can link back.
    post: Optional[HighlightPost] = None

    model_config = ConfigDict(from_attributes=True)


class HighlightAuthor(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PostHighlightResponse(HighlightResponse):
    """A highlight as every reader of the story sees it, with who made it."""

    user_id: int
    user: HighlightAuthor
