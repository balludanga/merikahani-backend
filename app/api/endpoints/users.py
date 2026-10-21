from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.post import Post
from app.models.follow import Follow
from app.schemas.user import UserProfileResponse
from app.core.dependencies import get_current_user_optional

router = APIRouter()


@router.get("/{user_id}", response_model=UserProfileResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="लेखक नहीं मिला")

    profile = UserProfileResponse.model_validate(user)
    profile.follower_count = (
        db.query(Follow).filter(Follow.following_id == user.id).count()
    )
    profile.following_count = (
        db.query(Follow).filter(Follow.follower_id == user.id).count()
    )
    profile.post_count = (
        db.query(Post).filter(Post.author_id == user.id, Post.published == 1).count()
    )
    profile.is_following = False
    if current_user:
        profile.is_following = (
            db.query(Follow)
            .filter(Follow.follower_id == current_user.id, Follow.following_id == user.id)
            .first()
            is not None
        )
    return profile
