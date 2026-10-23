from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional

class CommentBase(BaseModel):
    content: str

class CommentCreate(CommentBase):
    post_id: int
    quote: Optional[str] = None
    # The response being answered (any response; replies to a reply join the same thread).
    parent_id: Optional[int] = None

    @field_validator("quote")
    @classmethod
    def tidy_quote(cls, value: Optional[str]) -> Optional[str]:
        cleaned = " ".join((value or "").split())
        return cleaned[:1000] or None

class CommentUpdate(BaseModel):
    content: Optional[str] = None

class CommentAuthor(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None

    class Config:
        from_attributes = True

class CommentResponse(CommentBase):
    id: int
    quote: Optional[str] = None
    parent_id: Optional[int] = None
    post_id: int
    author_id: int
    author: CommentAuthor
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
