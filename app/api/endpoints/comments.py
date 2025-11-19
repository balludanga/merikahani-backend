from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.comment import Comment
from app.models.post import Post
from app.models.user import User
from app.schemas.comment import CommentCreate, CommentResponse
from app.core.dependencies import get_current_user

router = APIRouter()

@router.get("/post/{post_id}", response_model=List[CommentResponse])
def get_post_comments(post_id: int, db: Session = Depends(get_db)):
    """Get all comments for a specific post"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    comments = db.query(Comment).filter(Comment.post_id == post_id).order_by(Comment.created_at.asc()).all()
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
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
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
    """Delete a comment (only by author)"""
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    if comment.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this comment")
    
    db.delete(comment)
    db.commit()
    return None
