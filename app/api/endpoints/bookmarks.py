from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from typing import List
from app.database import get_db
from app.models.post import Post
from app.models.user import User
from app.models.clap import Clap
from app.models.bookmark import Bookmark
from app.schemas.post import PostResponse
from app.core.dependencies import get_current_user
from app.core.content import can_view_post
from app.api.endpoints.posts import list_response

router = APIRouter()


@router.get("", response_model=List[PostResponse])
def get_my_bookmarks(
    request: Request,
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(Post)
        .options(selectinload(Post.author))
        .join(Bookmark, Bookmark.post_id == Post.id)
        .filter(
            Bookmark.user_id == current_user.id,
            (Post.published == 1) | (Post.author_id == current_user.id),
        )
        .order_by(Bookmark.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    clap_rows = (
        db.query(Clap)
        .filter(
            Clap.user_id == current_user.id,
            Clap.post_id.in_([post.id for post in rows]) if rows else Clap.post_id.is_(None),
        )
        .all()
    )
    claps_by_post = {row.post_id: row.count for row in clap_rows}
    for post in rows:
        post.my_claps = claps_by_post.get(post.id, 0)
        post.bookmarked = True
    return list_response(rows, request, current_user)


@router.post("/{post_id}", status_code=status.HTTP_201_CREATED)
def add_bookmark(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")

    existing = (
        db.query(Bookmark)
        .filter(Bookmark.user_id == current_user.id, Bookmark.post_id == post_id)
        .first()
    )
    if not existing:
        db.add(Bookmark(user_id=current_user.id, post_id=post_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # a concurrent request already saved it
    return {"bookmarked": True}


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_bookmark(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = (
        db.query(Bookmark)
        .filter(Bookmark.user_id == current_user.id, Bookmark.post_id == post_id)
        .first()
    )
    if existing:
        db.delete(existing)
        db.commit()
    return None
