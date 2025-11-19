from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional
from app.schemas.user import UserResponse

class PostBase(BaseModel):
    title: str
    subtitle: Optional[str] = None
    content: str
    cover_image: Optional[str] = None
    published: int = 0

class PostCreate(PostBase):
    pass

class PostUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    content: Optional[str] = None
    cover_image: Optional[str] = None
    published: Optional[int] = None

class PostResponse(PostBase):
    id: int
    slug: str
    author_id: int
    author: UserResponse
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

