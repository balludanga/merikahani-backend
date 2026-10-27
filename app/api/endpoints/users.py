import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.endpoints.uploads import ALLOWED_TYPES, ImagePayload, _decode
from app.core.badges import earned_badges
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.ratelimit import actions, enforce
from app.database import get_db
from app.models.follow import Follow
from app.models.post import Post
from app.models.user import User
from app.models.user_media import UserMedia
from app.schemas.profile import ProfileUpdate
from app.schemas.user import UserProfileResponse

router = APIRouter()

# The browser crops to 512x512 / 1500x500 and compresses; these are ceilings.
MAX_AVATAR_BYTES = 300 * 1024
MAX_COVER_BYTES = 700 * 1024
PROFILE_LIMIT = (60, 60 * 60)  # edits per hour
IMAGE_LIMIT = (20, 60 * 60)  # image changes per hour


def _build_profile(db: Session, user: User, viewer: Optional[User]) -> UserProfileResponse:
    profile = UserProfileResponse.model_validate(user)
    profile.follower_count = db.query(Follow).filter(Follow.following_id == user.id).count()
    profile.following_count = db.query(Follow).filter(Follow.follower_id == user.id).count()
    published = db.query(Post).filter(Post.author_id == user.id, Post.published == 1)
    profile.post_count = published.count()
    profile.claps_total = int(
        db.query(func.coalesce(func.sum(Post.clap_count), 0))
        .filter(Post.author_id == user.id, Post.published == 1)
        .scalar()
        or 0
    )
    # Pins that were since unpublished or deleted just drop out.
    live = {row[0] for row in db.query(Post.id).filter(
        Post.author_id == user.id, Post.published == 1, Post.id.in_(user.pinned_post_ids or [0])
    )}
    profile.pinned_post_ids = [i for i in user.pinned_post_ids if i in live]
    profile.badges = earned_badges(profile.post_count, profile.claps_total, profile.follower_count)
    profile.is_following = bool(
        viewer
        and db.query(Follow)
        .filter(Follow.follower_id == viewer.id, Follow.following_id == user.id)
        .first()
    )
    return profile


# --- the signed-in writer edits their own profile (declared before /{user_id}) ---

@router.put("/me/profile", response_model=UserProfileResponse)
def update_my_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce(actions, f"profile:{current_user.id}", *PROFILE_LIMIT, record=True)
    sent = payload.model_fields_set

    for field in ("full_name", "tagline", "status_line", "location", "bio", "accent", "cover_style"):
        if field in sent:
            setattr(current_user, field, getattr(payload, field))
    if "genres" in sent:
        current_user.genres_json = json.dumps(payload.genres or [], ensure_ascii=False)
    if "links" in sent:
        current_user.links_json = json.dumps(
            [{"label": item.label, "url": item.url} for item in (payload.links or [])], ensure_ascii=False
        )
    if "pinned_post_ids" in sent:
        ids = payload.pinned_post_ids or []
        if ids:
            own = {row[0] for row in db.query(Post.id).filter(
                Post.author_id == current_user.id, Post.published == 1, Post.id.in_(ids)
            )}
            if set(ids) - own:
                raise HTTPException(status_code=400, detail="केवल अपनी प्रकाशित रचनाएँ चुनी जा सकती हैं")
        current_user.pinned_json = json.dumps(ids)
    db.commit()
    db.refresh(current_user)
    return _build_profile(db, current_user, current_user)


def _store_image(db: Session, user: User, payload: ImagePayload, kind: str, cap: int) -> None:
    if payload.content_type not in ALLOWED_TYPES or payload.content_type == "image/gif":
        raise HTTPException(status_code=400, detail="केवल JPEG, PNG या WebP चित्र लगाए जा सकते हैं।")
    binary = _decode(payload.data, payload.content_type)
    if len(binary) > cap:
        raise HTTPException(status_code=413, detail="चित्र बहुत बड़ा है — उसे थोड़ा छोटा करके फिर लगाएँ।")
    enforce(actions, f"profile-image:{user.id}", *IMAGE_LIMIT, record=True)
    media = db.query(UserMedia).filter(UserMedia.user_id == user.id).first()
    if media is None:
        media = UserMedia(user_id=user.id)
        db.add(media)
    version = int(time.time())
    setattr(media, kind, binary)
    setattr(media, f"{kind}_type", payload.content_type)
    setattr(media, f"{kind}_version", version)
    setattr(user, "avatar_url" if kind == "avatar" else "cover_url", f"/api/users/{user.id}/{kind}?v={version}")
    db.commit()


def _drop_image(db: Session, user: User, kind: str) -> None:
    media = db.query(UserMedia).filter(UserMedia.user_id == user.id).first()
    if media is not None:
        setattr(media, kind, None)
        setattr(media, f"{kind}_type", None)
        setattr(media, f"{kind}_version", None)
    setattr(user, "avatar_url" if kind == "avatar" else "cover_url", None)
    db.commit()


@router.put("/me/avatar", response_model=UserProfileResponse)
def set_my_avatar(
    payload: ImagePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _store_image(db, current_user, payload, "avatar", MAX_AVATAR_BYTES)
    return _build_profile(db, current_user, current_user)


@router.delete("/me/avatar", response_model=UserProfileResponse)
def remove_my_avatar(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _drop_image(db, current_user, "avatar")
    return _build_profile(db, current_user, current_user)


@router.put("/me/cover", response_model=UserProfileResponse)
def set_my_cover(
    payload: ImagePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _store_image(db, current_user, payload, "cover", MAX_COVER_BYTES)
    return _build_profile(db, current_user, current_user)


@router.delete("/me/cover", response_model=UserProfileResponse)
def remove_my_cover(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _drop_image(db, current_user, "cover")
    return _build_profile(db, current_user, current_user)


# --- anyone may read a profile and its images ---

def _serve(db: Session, request: Request, user_id: int, kind: str) -> Response:
    media = db.query(UserMedia).filter(UserMedia.user_id == user_id).first()
    data = getattr(media, kind, None) if media else None
    if not data:
        raise HTTPException(status_code=404, detail="चित्र नहीं मिला")
    etag = f'"{kind}-{getattr(media, kind + "_version")}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=86400"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=getattr(media, f"{kind}_type"), headers=headers)


@router.get("/{user_id}/avatar")
def get_avatar(user_id: int, request: Request, db: Session = Depends(get_db)):
    return _serve(db, request, user_id, "avatar")


@router.get("/{user_id}/cover")
def get_cover(user_id: int, request: Request, db: Session = Depends(get_db)):
    return _serve(db, request, user_id, "cover")


@router.get("/{user_id}", response_model=UserProfileResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="लेखक नहीं मिला")
    return _build_profile(db, user, current_user)
