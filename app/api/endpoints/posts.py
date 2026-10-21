from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from datetime import datetime
import re
from app.database import get_db
from app.models.post import Post
from app.models.user import User
from app.models.clap import Clap
from app.models.follow import Follow
from app.models.bookmark import Bookmark
from app.models.tag import Tag, post_tags
from app.schemas.post import PostCreate, PostUpdate, PostResponse, ClapResponse
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.content import apply_read_gate
from app.core.sanitize import clean_html

router = APIRouter()

MAX_TAGS_PER_POST = 5


def generate_slug(title: str) -> str:
    slug = (title or "").strip().lower()
    # Keep the full Devanagari block (incl. matras and nukta) so Hindi
    # titles and tags produce readable slugs.
    slug = re.sub(r"[^\w\s\u0900-\u097F-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")[:80]
    if not slug:
        slug = f"lekh-{int(datetime.utcnow().timestamp())}"
    return slug


def normalize_tags(names, db: Session):
    """Clean, dedupe and resolve tag names into Tag rows (creating missing ones)."""
    cleaned = []
    seen = set()
    for raw in names or []:
        name = re.sub(r"\s+", " ", str(raw or "").strip())
        if len(name) < 2 or len(name) > 40:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)

    tags = []
    for name in cleaned[:MAX_TAGS_PER_POST]:
        slug = generate_slug(name)
        tag = db.query(Tag).filter(Tag.slug == slug).first()
        if not tag:
            tag = db.query(Tag).filter(Tag.name == name).first()
        if not tag:
            tag = Tag(name=name, slug=slug)
            db.add(tag)
            db.flush()
        tags.append(tag)
    return tags


def decorate_posts(posts, user: Optional[User], db: Session):
    """Attach viewer-specific clap/bookmark state and apply the read gate."""
    if not posts:
        return posts
    apply_read_gate(posts, user)
    if not user:
        for post in posts:
            post.my_claps = 0
            post.bookmarked = False
        return posts
    ids = [p.id for p in posts]
    clap_rows = db.query(Clap).filter(Clap.user_id == user.id, Clap.post_id.in_(ids)).all()
    claps_by_post = {row.post_id: row.count for row in clap_rows}
    bookmarked_ids = {
        row.post_id
        for row in db.query(Bookmark).filter(Bookmark.user_id == user.id, Bookmark.post_id.in_(ids)).all()
    }
    for post in posts:
        post.my_claps = claps_by_post.get(post.id, 0)
        post.bookmarked = post.id in bookmarked_ids
    return posts


@router.get("", response_model=List[PostResponse])
def get_posts(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    published: Optional[int] = Query(None),
    topic: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    feed: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(Post)
    if published is not None:
        query = query.filter(Post.published == published)
    else:
        query = query.filter(Post.published == 1)

    if topic:
        query = query.filter(Post.topic == topic)

    if feed == "following":
        followed_ids = (
            [row.following_id for row in db.query(Follow.following_id).filter(Follow.follower_id == current_user.id)]
            if current_user
            else []
        )
        query = query.filter(Post.author_id.in_(followed_ids))

    if tag:
        tag_row = db.query(Tag).filter(or_(Tag.slug == tag.strip(), Tag.name == tag.strip())).first()
        if not tag_row:
            return []
        query = query.join(post_tags, post_tags.c.post_id == Post.id).filter(post_tags.c.tag_id == tag_row.id)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Post.title.ilike(like), Post.subtitle.ilike(like), Post.content.ilike(like))
        )

    posts = query.order_by(Post.created_at.desc()).offset(skip).limit(limit).all()
    decorate_posts(posts, current_user, db)
    return posts


@router.get("/me", response_model=List[PostResponse])
def get_my_posts(
    published: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Post).filter(Post.author_id == current_user.id)
    if published is not None:
        query = query.filter(Post.published == published)
    posts = query.order_by(Post.updated_at.desc()).all()
    decorate_posts(posts, current_user, db)
    return posts


@router.get("/slug/{slug}", response_model=PostResponse)
def get_post_by_slug(
    slug: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    post = db.query(Post).filter(Post.slug == slug).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    decorate_posts([post], current_user, db)
    return post


@router.get("/user/{user_id}", response_model=List[PostResponse])
def get_user_posts(
    user_id: int,
    published: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(Post).filter(Post.author_id == user_id)
    if published is not None:
        query = query.filter(Post.published == published)
    else:
        query = query.filter(Post.published == 1)
    posts = query.order_by(Post.created_at.desc()).all()
    decorate_posts(posts, current_user, db)
    return posts


@router.get("/{post_id}", response_model=PostResponse)
def get_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    decorate_posts([post], current_user, db)
    return post


@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(
    post_data: PostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    slug = generate_slug(post_data.title)
    existing = db.query(Post).filter(Post.slug == slug).first()
    if existing:
        slug = f"{slug}-{int(datetime.utcnow().timestamp())}"

    db_post = Post(
        title=post_data.title,
        subtitle=post_data.subtitle,
        content=post_data.content,
        content_html=clean_html(post_data.content_html),
        slug=slug,
        cover_image=post_data.cover_image,
        topic=post_data.topic,
        author_id=current_user.id,
        published=post_data.published,
        clap_count=0,
    )
    db_post.tags = normalize_tags(post_data.tags, db)
    db.add(db_post)
    db.commit()
    db.refresh(db_post)
    db_post.my_claps = 0
    db_post.bookmarked = False
    return db_post


@router.put("/{post_id}", response_model=PostResponse)
def update_post(
    post_id: int,
    post_data: PostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this post")

    update_data = post_data.dict(exclude_unset=True)
    if "title" in update_data and update_data["title"] != post.title:
        slug = generate_slug(update_data["title"])
        existing = db.query(Post).filter(Post.slug == slug, Post.id != post_id).first()
        if existing:
            slug = f"{slug}-{int(datetime.utcnow().timestamp())}"
        update_data["slug"] = slug

    new_tags = update_data.pop("tags", None)
    if "content_html" in update_data:
        update_data["content_html"] = clean_html(update_data["content_html"])
    for field, value in update_data.items():
        setattr(post, field, value)

    if new_tags is not None:
        post.tags = normalize_tags(new_tags, db)

    db.commit()
    db.refresh(post)
    decorate_posts([post], current_user, db)
    return post


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this post")

    db.delete(post)
    db.commit()
    return None


@router.post("/{post_id}/clap", response_model=ClapResponse)
def clap_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    clap = (
        db.query(Clap)
        .filter(Clap.user_id == current_user.id, Clap.post_id == post_id)
        .first()
    )
    if clap:
        if clap.count >= 50:
            return ClapResponse(clap_count=post.clap_count or 0, my_claps=clap.count)
        clap.count += 1
    else:
        clap = Clap(user_id=current_user.id, post_id=post_id, count=1)
        db.add(clap)

    post.clap_count = (post.clap_count or 0) + 1
    db.commit()
    db.refresh(post)
    db.refresh(clap)
    return ClapResponse(clap_count=post.clap_count, my_claps=clap.count)
