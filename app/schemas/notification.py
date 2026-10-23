from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class NotificationActor(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class NotificationPost(BaseModel):
    id: int
    title: str
    slug: str

    model_config = ConfigDict(from_attributes=True)


class NotificationResponse(BaseModel):
    id: int
    kind: str
    count: int
    text: Optional[str] = None
    comment_id: Optional[int] = None
    created_at: datetime
    read: bool
    actor: NotificationActor
    post: NotificationPost


class UnreadCount(BaseModel):
    count: int


class MarkRead(BaseModel):
    # Leave out to mark everything read.
    ids: Optional[List[int]] = None
