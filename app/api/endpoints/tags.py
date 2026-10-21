from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from app.database import get_db
from app.models.post import Post
from app.models.tag import Tag, post_tags
from app.models.clap import Clap
from app.schemas.post import PostResponse
from app.schemas.tag import TagResponse
from app.core.dependencies import get_current_user_optional
from app.core.content import apply_read_gate

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
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    tag = db.query(Tag).filter(Tag.slug == slug).first()
    if not tag:
        raise HTTPException(status_code=404, detail="यह टैग नहीं मिला")

    posts = (
        db.query(Post)
        .join(post_tags, post_tags.c.post_id == Post.id)
        .filter(post_tags.c.tag_id == tag.id, Post.published == 1)
        .order_by(Post.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    apply_read_gate(posts, current_user)
    if current_user:
        ids = [p.id for p in posts]
        claps = {
            row.post_id: row.count
            for row in db.query(Clap).filter(Clap.user_id == current_user.id, Clap.post_id.in_(ids)).all()
        }
        for post in posts:
            post.my_claps = claps.get(post.id, 0)
            post.bookmarked = False
    else:
        for post in posts:
            post.my_claps = 0
            post.bookmarked = False
    return posts
