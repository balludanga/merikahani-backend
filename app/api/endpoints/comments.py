from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload
from typing import List, Optional
from app.database import get_db
from app.models.comment import Comment
from app.models.post import Post
from app.models.user import User
from app.schemas.comment import CommentCreate, CommentResponse
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.content import can_view_post
from app.core.ratelimit import actions, enforce

router = APIRouter()

@router.get("/post/{post_id}", response_model=List[CommentResponse])
def get_post_comments(
    post_id: int,
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(200, ge=1, le=500),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get the comments for a specific post, oldest first"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")
    
    comments = (
        db.query(Comment)
        .options(selectinload(Comment.author))
        .filter(Comment.post_id == post_id)
        .order_by(Comment.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return comments

@router.post("", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def create_comment(
    comment_data: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new comment"""
    # Check if post exists
    post = db.query(Post).filter(Post.id == comment_data.post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")
    
    enforce(actions, f"comment:{current_user.id}", 30, 10 * 60, record=True)
    db_comment = Comment(
        content=comment_data.content,
        post_id=comment_data.post_id,
        author_id=current_user.id
    )
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    return db_comment

@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a comment (by its author, or by the author of the post it is on)"""
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="टिप्पणी नहीं मिली")
    
    post = db.query(Post).filter(Post.id == comment.post_id).first()
    is_post_author = post is not None and post.author_id == current_user.id
    if comment.author_id != current_user.id and not is_post_author:
        raise HTTPException(status_code=403, detail="आप यह टिप्पणी नहीं हटा सकते")
    
    db.delete(comment)
    db.commit()
    return None
