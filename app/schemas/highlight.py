from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime


class HighlightCreate(BaseModel):
    post_id: int
    text: str

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("हाइलाइट का अंश कम से कम 2 अक्षर का होना चाहिए")
        return cleaned[:2000]


class HighlightResponse(BaseModel):
    id: int
    post_id: int
    text: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
