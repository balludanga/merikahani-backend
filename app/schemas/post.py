import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime
from typing import List, Optional
from app.schemas.user import PublicUserResponse

# An inline cover is a compressed photo; ~3 MB of base64 is already generous.
MAX_COVER_CHARS = 4_000_000
_COVER_RE = re.compile(r"^(https?://\S+|data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+)$")


def _check_cover(value):
    if value in (None, ""):
        return value
    if len(value) > MAX_COVER_CHARS or not _COVER_RE.match(value):
        raise ValueError("कवर चित्र एक मान्य कड़ी (http/https) या छोटा चित्र होना चाहिए")
    return value


class PostBase(BaseModel):
    title: str = Field(max_length=300)
    subtitle: Optional[str] = Field(None, max_length=500)
    content: str = Field(max_length=300_000)
    content_html: Optional[str] = Field(None, max_length=600_000)
    cover_image: Optional[str] = None
    topic: Optional[str] = Field(None, max_length=50)
    published: int = Field(0, ge=0, le=1)

    @field_validator("cover_image")
    @classmethod
    def cover_is_safe(cls, value):
        return _check_cover(value)

class PostCreate(PostBase):
    tags: Optional[List[str]] = None

class PostUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=300)
    subtitle: Optional[str] = Field(None, max_length=500)
    content: Optional[str] = Field(None, max_length=300_000)
    content_html: Optional[str] = Field(None, max_length=600_000)
    cover_image: Optional[str] = None
    topic: Optional[str] = Field(None, max_length=50)
    published: Optional[int] = Field(None, ge=0, le=1)
    tags: Optional[List[str]] = None

    @field_validator("title", "content", "published", mode="before")
    @classmethod
    def required_when_sent(cls, value):
        # These columns are NOT NULL: omit them to leave unchanged, never send null.
        if value is None:
            raise ValueError("यह फ़ील्ड खाली नहीं हो सकता")
        return value

    @field_validator("cover_image")
    @classmethod
    def cover_is_safe(cls, value):
        return _check_cover(value)

class PostResponse(BaseModel):
    """Read shape. Deliberately NOT built on PostBase: input limits must never
    make an already-stored post unreadable."""
    id: int
    title: str
    subtitle: Optional[str] = None
    content: str
    content_html: Optional[str] = None
    cover_image: Optional[str] = None
    topic: Optional[str] = None
    published: int = 0
    slug: str
    author_id: int
    author: PublicUserResponse
    clap_count: int = 0
    my_claps: int = 0
    bookmarked: bool = False
    is_preview: bool = False
    tags: List[str] = []
    tag_slugs: List[str] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("tags", mode="before")
    @classmethod
    def tags_to_names(cls, value):
        # The ORM relationship holds Tag objects; expose plain names instead.
        if value and not isinstance(value[0], str):
            return [getattr(tag, "name", str(tag)) for tag in value]
        return value or []

    @field_validator("tag_slugs", mode="before")
    @classmethod
    def tags_to_slugs(cls, value):
        if value and not isinstance(value[0], str):
            return [getattr(tag, "slug", str(tag)) for tag in value]
        return value or []


class ClapResponse(BaseModel):
    clap_count: int
    my_claps: int
