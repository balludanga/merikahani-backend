from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
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
from app.core.notify import notify
from app.models.notification import COMMENT, REPLY, Notification

router = APIRouter()

@router.get("/post/{post_id}", response_model=List[CommentResponse])
def get_post_comments(
    post_id: int,
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(200, ge=1, le=500),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Responses for a story.

    asc (the default) reads oldest first, `limit` rows per page. desc reads newest
    thread first, `limit` threads per page, and always returns a thread together with
    its answers, so an answer is never cut off from the response it belongs to.
    """
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")

    if order == "desc":
        roots = [
            row[0]
            for row in db.query(Comment.id)
            .filter(Comment.post_id == post_id, Comment.parent_id.is_(None))
            .order_by(Comment.created_at.desc(), Comment.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        ]
        if not roots:
            return []
        thread_of = {root_id: index for index, root_id in enumerate(roots)}
        rows = (
            db.query(Comment)
            .options(selectinload(Comment.author))
            .filter(
                Comment.post_id == post_id,
                or_(Comment.id.in_(roots), Comment.parent_id.in_(roots)),
            )
            .all()
        )
        # Newest thread first; inside a thread the response then its answers, oldest first.
        # Stamps are second-precision, so the id breaks ties within the same instant.
        rows.sort(
            key=lambda c: (
                thread_of[c.parent_id or c.id],
                c.parent_id is not None,
                c.created_at,
                c.id,
            )
        )
        return rows

    comments = (
        db.query(Comment)
        .options(selectinload(Comment.author))
        .filter(Comment.post_id == post_id)
        .order_by(Comment.created_at.asc(), Comment.id.asc())
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
    
    # Answering a response: it must be on this story. Replies to a reply join the same
    # thread (threads are one level deep), but the person told is who was answered.
    answered = None
    thread_root = None
    if comment_data.parent_id is not None:
        answered = (
            db.query(Comment)
            .filter(Comment.id == comment_data.parent_id, Comment.post_id == post.id)
            .first()
        )
        if answered is None:
            raise HTTPException(status_code=404, detail="जिस प्रतिक्रिया का जवाब देना है वह नहीं मिली")
        thread_root = answered.parent_id or answered.id

    enforce(actions, f"comment:{current_user.id}", 30, 10 * 60, record=True)
    db_comment = Comment(
        content=comment_data.content,
        # A passage quote belongs to a top-level response, not to an answer.
        quote=None if thread_root else comment_data.quote,
        quote_start=None if thread_root or not comment_data.quote else comment_data.quote_start,
        parent_id=thread_root,
        post_id=comment_data.post_id,
        author_id=current_user.id
    )
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    if answered is not None:
        notify(
            db, post=post, recipient_id=answered.author_id, actor_id=current_user.id,
            kind=REPLY, comment_id=db_comment.id, text=db_comment.content,
        )
    # The story's author hears about every response, unless the reply above already told them.
    if answered is None or answered.author_id != post.author_id:
        notify(
            db, post=post, actor_id=current_user.id, kind=COMMENT,
            comment_id=db_comment.id, text=db_comment.content,
        )
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
    
    # Nobody is told about a response that is gone, nor about the answers that go with it.
    gone = [comment.id] + [reply.id for reply in comment.replies]
    db.query(Notification).filter(Notification.comment_id.in_(gone)).delete(synchronize_session=False)
    db.delete(comment)
    db.commit()
    return None
