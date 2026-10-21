from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime
from typing import List, Optional
from app.schemas.user import UserResponse

class PostBase(BaseModel):
    title: str
    subtitle: Optional[str] = None
    content: str
    cover_image: Optional[str] = None
    topic: Optional[str] = None
    published: int = 0

class PostCreate(PostBase):
    tags: Optional[List[str]] = None

class PostUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    content: Optional[str] = None
    cover_image: Optional[str] = None
    topic: Optional[str] = None
    published: Optional[int] = None
    tags: Optional[List[str]] = None

class PostResponse(PostBase):
    id: int
    slug: str
    author_id: int
    author: UserResponse
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
