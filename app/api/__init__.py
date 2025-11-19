from fastapi import APIRouter

api_router = APIRouter()

from .endpoints import auth, posts, comments, seo

api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(comments.router, prefix="/comments", tags=["comments"])
api_router.include_router(seo.router, tags=["seo"])