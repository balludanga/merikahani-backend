from fastapi import APIRouter

api_router = APIRouter()

from .endpoints import auth, posts, comments, follows, bookmarks, tags, users, highlights, notifications, seo, uploads, prerender

api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(posts.router, prefix="/posts", tags=["posts"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(comments.router, prefix="/comments", tags=["comments"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(follows.router, prefix="/follows", tags=["follows"])
api_router.include_router(bookmarks.router, prefix="/bookmarks", tags=["bookmarks"])
api_router.include_router(highlights.router, prefix="/highlights", tags=["highlights"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(tags.router, prefix="/tags", tags=["tags"])
api_router.include_router(seo.router, tags=["seo"])
api_router.include_router(prerender.router, prefix="/prerender", tags=["seo"])
