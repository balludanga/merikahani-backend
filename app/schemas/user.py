from pydantic import BaseModel, EmailStr, ConfigDict, Field
from datetime import datetime
from typing import Optional

class UserBase(BaseModel):
    email: EmailStr
    # Letters (Latin or Devanagari), digits, underscore, dot; no spaces or slashes.
    username: str = Field(pattern=r"^[\w.]{3,30}$")
    full_name: Optional[str] = Field(None, max_length=100)

class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)

class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)

class PublicUserResponse(BaseModel):
    """What anyone may see about a writer. Never includes the email address."""
    id: int
    username: str
    full_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UserResponse(PublicUserResponse):
    """The signed-in user's own account (login, register, /auth/me only)."""
    email: EmailStr

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class UserProfileResponse(PublicUserResponse):
    follower_count: int = 0
    following_count: int = 0
    post_count: int = 0
    is_following: bool = False


