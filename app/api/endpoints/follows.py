from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.user import User
from app.models.follow import Follow
from app.schemas.user import PublicUserResponse
from app.core.dependencies import get_current_user

router = APIRouter()


def _get_target_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="लेखक नहीं मिला")
    return user


@router.post("/{user_id}", status_code=status.HTTP_201_CREATED)
def follow_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = _get_target_user(db, user_id)
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="आप स्वयं को फ़ॉलो नहीं कर सकते")

    existing = (
        db.query(Follow)
        .filter(Follow.follower_id == current_user.id, Follow.following_id == target.id)
        .first()
    )
    if not existing:
        db.add(Follow(follower_id=current_user.id, following_id=target.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # a concurrent request already followed

    followers = db.query(Follow).filter(Follow.following_id == target.id).count()
    return {"following": True, "follower_count": followers}


@router.delete("/{user_id}")
def unfollow_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = _get_target_user(db, user_id)
    existing = (
        db.query(Follow)
        .filter(Follow.follower_id == current_user.id, Follow.following_id == target.id)
        .first()
    )
    if existing:
        db.delete(existing)
        db.commit()

    followers = db.query(Follow).filter(Follow.following_id == target.id).count()
    return {"following": False, "follower_count": followers}


@router.get("/{user_id}/followers", response_model=List[PublicUserResponse])
def get_followers(
    user_id: int,
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _get_target_user(db, user_id)
    rows = (
        db.query(User)
        .join(Follow, Follow.follower_id == User.id)
        .filter(Follow.following_id == user_id)
        .order_by(Follow.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows


@router.get("/{user_id}/following", response_model=List[PublicUserResponse])
def get_following(
    user_id: int,
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _get_target_user(db, user_id)
    rows = (
        db.query(User)
        .join(Follow, Follow.following_id == User.id)
        .filter(Follow.follower_id == user_id)
        .order_by(Follow.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows
