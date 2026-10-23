from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload
from typing import List, Optional

from app.database import get_db
from app.models.highlight import Highlight
from app.models.post import Post
from app.models.user import User
from app.schemas.highlight import HighlightCreate, HighlightResponse, PostHighlightResponse
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.content import PREVIEW_LIMIT, can_view_post, preview_text
from app.core.ratelimit import actions, enforce
from app.core.notify import notify
from app.models.notification import HIGHLIGHT

router = APIRouter()


@router.get("", response_model=List[HighlightResponse])
def get_my_highlights(
    post_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(300, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The signed-in reader's own highlights, newest first (optionally for one post)."""
    query = db.query(Highlight).options(selectinload(Highlight.post)).filter(Highlight.user_id == current_user.id)
    if post_id is not None:
        query = query.filter(Highlight.post_id == post_id)
    rows = query.order_by(Highlight.created_at.desc(), Highlight.id.desc()).offset(skip).limit(limit).all()
    # A story that has since been unpublished by someone else is no longer readable.
    return [row for row in rows if row.post is not None and can_view_post(row.post, current_user)]


@router.get("/post/{post_id}", response_model=List[PostHighlightResponse])
def get_post_highlights(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Every reader's highlights on a story, with who made each one.

    A signed-out visitor only reads the teaser, so they only get the highlights
    that fall inside it; the gated remainder is never echoed back here.
    """
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")

    rows = (
        db.query(Highlight)
        .options(selectinload(Highlight.user))
        .filter(Highlight.post_id == post_id)
        .order_by(Highlight.created_at.asc(), Highlight.id.asc())
        .limit(1000)
        .all()
    )
    content = post.content or ""
    if current_user is None and len(content) > PREVIEW_LIMIT:
        teaser = " ".join(preview_text(content).split())
        rows = [row for row in rows if row.text in teaser]
    return rows


@router.post("", response_model=HighlightResponse, status_code=status.HTTP_201_CREATED)
def create_highlight(
    payload: HighlightCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = db.query(Post).filter(Post.id == payload.post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")

    # Re-highlighting the same passage returns the existing row. The same words
    # at another place in the story are a different highlight.
    same = db.query(Highlight).filter(
        Highlight.user_id == current_user.id,
        Highlight.post_id == payload.post_id,
        Highlight.text == payload.text,
    )
    existing = same.filter(Highlight.start_offset == payload.start).first() if payload.start is not None else same.first()
    if existing is None and payload.start is not None:
        # An older highlight that never recorded where it is: adopt this position.
        existing = same.filter(Highlight.start_offset.is_(None)).first()
        if existing is not None:
            existing.start_offset = payload.start
            existing.prefix = payload.prefix
            existing.suffix = payload.suffix
            db.commit()
            db.refresh(existing)
    if existing:
        return existing

    enforce(actions, f"highlight:{current_user.id}", 120, 10 * 60, record=True)
    highlight = Highlight(
        user_id=current_user.id,
        post_id=payload.post_id,
        text=payload.text,
        start_offset=payload.start,
        prefix=payload.prefix,
        suffix=payload.suffix,
    )
    db.add(highlight)
    db.commit()
    db.refresh(highlight)
    notify(db, post=post, actor_id=current_user.id, kind=HIGHLIGHT, text=highlight.text)
    return highlight


@router.delete("/{highlight_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_highlight(
    highlight_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    highlight = db.query(Highlight).filter(Highlight.id == highlight_id).first()
    if not highlight:
        raise HTTPException(status_code=404, detail="हाइलाइट नहीं मिली")
    if highlight.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="आप यह हाइलाइट नहीं हटा सकते")
    db.delete(highlight)
    db.commit()
    return None
