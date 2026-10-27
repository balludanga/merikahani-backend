from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional

# The response box in the reader caps input at 5000 characters. The server has to hold
# the same line, or a direct API call can store a novel in one row.
MAX_COMMENT_CHARS = 5000

class CommentBase(BaseModel):
    content: str

class CommentCreate(CommentBase):
    # min_length catches "" before the validator runs; the validator catches "   ".
    content: str = Field(min_length=1, max_length=MAX_COMMENT_CHARS)
    post_id: int
    quote: Optional[str] = None
    quote_start: Optional[int] = None
    # The response being answered (any response; replies to a reply join the same thread).
    parent_id: Optional[int] = None

    @field_validator("content")
    @classmethod
    def content_has_words(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("प्रतिक्रिया में कुछ लिखना ज़रूरी है")
        return cleaned

    @field_validator("quote")
    @classmethod
    def tidy_quote(cls, value: Optional[str]) -> Optional[str]:
        cleaned = " ".join((value or "").split())
        return cleaned[:1000] or None

    @field_validator("quote_start")
    @classmethod
    def start_not_negative(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        return value if 0 <= value < 10_000_000 else None

class CommentAuthor(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None

    class Config:
        from_attributes = True

class CommentResponse(CommentBase):
    id: int
    quote: Optional[str] = None
    quote_start: Optional[int] = None
    parent_id: Optional[int] = None
    post_id: int
    author_id: int
    author: CommentAuthor
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
