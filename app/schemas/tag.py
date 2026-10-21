from pydantic import BaseModel, ConfigDict


class TagResponse(BaseModel):
    id: int
    name: str
    slug: str
    post_count: int = 0

    model_config = ConfigDict(from_attributes=True)
