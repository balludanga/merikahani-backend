from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class ExampleResponse(BaseModel):
    message: str

@router.get("/", response_model=ExampleResponse)
async def get_example() -> ExampleResponse:
    return ExampleResponse(message="Hello, this is an example response!")