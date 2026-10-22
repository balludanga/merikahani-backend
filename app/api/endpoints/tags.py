from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func
from typing import List
from app.database import get_db
from app.models.post import Post
from app.models.tag import Tag, post_tags
from app.schemas.post import PostResponse
from app.schemas.tag import TagResponse
from app.core.dependencies import get_current_user_optional
from app.api.endpoints.posts import decorate_posts, list_response

router = APIRouter()


@router.get("", response_model=List[TagResponse])
def get_tags(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Popular tags by number of published posts."""
    rows = (
        db.query(Tag, func.count(post_tags.c.post_id).label("post_count"))
        .join(post_tags, post_tags.c.tag_id == Tag.id)
        .join(Post, Post.id == post_tags.c.post_id)
        .filter(Post.published == 1)
        .group_by(Tag.id)
        .order_by(func.count(post_tags.c.post_id).desc(), Tag.name.asc())
        .limit(limit)
        .all()
    )
    return [TagResponse(id=tag.id, name=tag.name, slug=tag.slug, post_count=count) for tag, count in rows]


@router.get("/{slug}/posts", response_model=List[PostResponse])
def get_posts_by_tag(
    slug: str,
    request: Request,
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    tag = db.query(Tag).filter(Tag.slug == slug).first()
    if not tag:
        raise HTTPException(status_code=404, detail="यह टैग नहीं मिला")

    posts = (
        db.query(Post)
        .options(selectinload(Post.author))
        .join(post_tags, post_tags.c.post_id == Post.id)
        .filter(post_tags.c.tag_id == tag.id, Post.published == 1)
        .order_by(Post.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    decorate_posts(posts, current_user, db)
    return list_response(posts, request)
