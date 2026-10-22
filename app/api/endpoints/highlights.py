from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.highlight import Highlight
from app.models.post import Post
from app.models.user import User
from app.schemas.highlight import HighlightCreate, HighlightResponse
from app.core.dependencies import get_current_user
from app.core.content import can_view_post

router = APIRouter()


@router.get("", response_model=List[HighlightResponse])
def get_my_highlights(
    post_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(300, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The signed-in reader's highlights, newest first (optionally for one post)."""
    query = db.query(Highlight).filter(Highlight.user_id == current_user.id)
    if post_id is not None:
        query = query.filter(Highlight.post_id == post_id)
    return query.order_by(Highlight.created_at.desc(), Highlight.id.desc()).offset(skip).limit(limit).all()


@router.post("", response_model=HighlightResponse, status_code=status.HTTP_201_CREATED)
def create_highlight(
    payload: HighlightCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = db.query(Post).filter(Post.id == payload.post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")

    # Re-highlighting the same passage returns the existing row.
    existing = (
        db.query(Highlight)
        .filter(
            Highlight.user_id == current_user.id,
            Highlight.post_id == payload.post_id,
            Highlight.text == payload.text,
        )
        .first()
    )
    if existing:
        return existing

    highlight = Highlight(user_id=current_user.id, post_id=payload.post_id, text=payload.text)
    db.add(highlight)
    db.commit()
    db.refresh(highlight)
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
