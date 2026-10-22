from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from datetime import datetime
import base64
import binascii
import re
import uuid
from app.database import get_db
from app.models.post import Post
from app.models.user import User
from app.models.clap import Clap
from app.models.follow import Follow
from app.models.bookmark import Bookmark
from app.models.tag import Tag, post_tags
from app.schemas.post import PostCreate, PostUpdate, PostResponse, ClapResponse
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.content import apply_read_gate, can_view_post
from app.core.ratelimit import actions, enforce
from app.core.sanitize import clean_html

router = APIRouter()

MAX_TAGS_PER_POST = 5
MAX_CLAPS_PER_POST = 50
# Per-account write limits (window seconds): posts/hour, claps/hour.
POST_CREATE_LIMIT = (30, 60 * 60)
CLAP_LIMIT = (300, 60 * 60)
# Default and maximum rows for lists that used to be unbounded.
OWN_LIST_DEFAULT = 200
OWN_LIST_MAX = 500


def generate_slug(title: str) -> str:
    slug = (title or "").strip().lower()
    # Keep the full Devanagari block (incl. matras and nukta) so Hindi
    # titles and tags produce readable slugs.
    slug = re.sub(r"[^\w\sऀ-ॿ-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")[:80]
    if not slug:
        slug = f"lekh-{int(datetime.utcnow().timestamp())}"
    return slug


def unique_suffix(slug: str) -> str:
    return f"{slug}-{uuid.uuid4().hex[:8]}"


def like_pattern(term: str) -> str:
    """Escape LIKE wildcards so a search for '%' or '_' matches literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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


def public_base_url(request: Request) -> str:
    """Scheme + host the caller used (Render/Vercel send the forwarded values)."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host.split(',')[0].strip()}"


def cover_url(request: Request, post: Post) -> str:
    version = int((post.updated_at or datetime.utcnow()).timestamp())
    return f"{public_base_url(request)}/api/posts/{post.id}/cover?v={version}"


SELF_COVER_RE = re.compile(r"/api/posts/\d+/cover(\?|$)")


def reader_view(post: Post, viewer: Optional[User], request: Request) -> PostResponse:
    """Single-story response. Readers get inline covers as a cacheable image URL;
    the author keeps the stored value so the editor can save it back unchanged."""
    item = PostResponse.model_validate(post)
    is_author = viewer is not None and viewer.id == post.author_id
    if (item.cover_image or "").startswith("data:") and post.published == 1 and not is_author:
        item.cover_image = cover_url(request, post)
    return item


def list_response(posts, request: Request) -> List[PostResponse]:
    """Light list payload: no rich body, and published inline cover images become URLs.

    Built from copies so the ORM rows (and the stored cover) are never modified.
    """
    items = []
    for post in posts:
        item = PostResponse.model_validate(post)
        item.content_html = None
        if (item.cover_image or "").startswith("data:") and post.published == 1:
            item.cover_image = cover_url(request, post)
        items.append(item)
    return items


@router.get("", response_model=List[PostResponse])
def get_posts(
    request: Request,
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(10, ge=1, le=100),
    published: Optional[int] = Query(None),
    topic: Optional[str] = Query(None, max_length=50),
    tag: Optional[str] = Query(None, max_length=80),
    feed: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(Post).options(selectinload(Post.author))
    if published is None or published == 1:
        query = query.filter(Post.published == 1)
    else:
        # Drafts are private: only the signed-in author ever sees their own.
        if current_user is None:
            return []
        query = query.filter(Post.published == published, Post.author_id == current_user.id)

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

    if q and q.strip():
        like = like_pattern(q.strip())
        conditions = [Post.title.ilike(like, escape="\\"), Post.subtitle.ilike(like, escape="\\")]
        # Signed-out readers only see a teaser, so don't let search reveal the rest.
        if current_user is not None:
            conditions.append(Post.content.ilike(like, escape="\\"))
        query = query.filter(or_(*conditions))

    posts = query.order_by(Post.created_at.desc()).offset(skip).limit(limit).all()
    decorate_posts(posts, current_user, db)
    return list_response(posts, request)


@router.get("/me", response_model=List[PostResponse])
def get_my_posts(
    request: Request,
    published: Optional[int] = Query(None),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(OWN_LIST_DEFAULT, ge=1, le=OWN_LIST_MAX),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Post).options(selectinload(Post.author)).filter(Post.author_id == current_user.id)
    if published is not None:
        query = query.filter(Post.published == published)
    posts = query.order_by(Post.updated_at.desc()).offset(skip).limit(limit).all()
    decorate_posts(posts, current_user, db)
    return list_response(posts, request)


@router.get("/slug/{slug}", response_model=PostResponse)
def get_post_by_slug(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    post = db.query(Post).filter(Post.slug == slug).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")
    decorate_posts([post], current_user, db)
    return reader_view(post, current_user, request)


@router.get("/user/{user_id}", response_model=List[PostResponse])
def get_user_posts(
    user_id: int,
    request: Request,
    published: Optional[int] = Query(None),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(OWN_LIST_DEFAULT, ge=1, le=OWN_LIST_MAX),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(Post).options(selectinload(Post.author)).filter(Post.author_id == user_id)
    is_owner = current_user is not None and current_user.id == user_id
    if is_owner and published is not None:
        query = query.filter(Post.published == published)
    else:
        query = query.filter(Post.published == 1)
    posts = query.order_by(Post.created_at.desc()).offset(skip).limit(limit).all()
    decorate_posts(posts, current_user, db)
    return list_response(posts, request)


@router.get("/{post_id}/cover")
def get_post_cover(
    post_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Serve an inline (data:) cover image as a real, cacheable image response."""
    post = db.query(Post).filter(Post.id == post_id, Post.published == 1).first()
    cover = post.cover_image if post else None
    if not cover:
        raise HTTPException(status_code=404, detail="कवर चित्र नहीं मिला")
    if not cover.startswith("data:"):
        if SELF_COVER_RE.search(cover):  # a stored link back to this endpoint would loop forever
            raise HTTPException(status_code=404, detail="कवर चित्र नहीं मिला")
        return Response(status_code=307, headers={"Location": cover})
    try:
        header, encoded = cover.split(",", 1)
        content_type = header[5:].split(";")[0] or "image/jpeg"
        binary = base64.b64decode(encoded)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=404, detail="कवर चित्र नहीं मिला")
    etag = f'"{post.id}-{int((post.updated_at or datetime.utcnow()).timestamp())}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=86400"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=binary, media_type=content_type, headers=headers)


@router.get("/{post_id}", response_model=PostResponse)
def get_post(
    post_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")
    decorate_posts([post], current_user, db)
    return reader_view(post, current_user, request)


@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(
    post_data: PostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce(actions, f"post:{current_user.id}", *POST_CREATE_LIMIT, record=True)
    if SELF_COVER_RE.search(post_data.cover_image or ""):
        post_data.cover_image = None
    slug = generate_slug(post_data.title)
    if db.query(Post).filter(Post.slug == slug).first():
        slug = unique_suffix(slug)

    db_post = None
    for attempt in range(2):
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
        try:
            db.commit()
            break
        except IntegrityError:
            # Two requests chose the same slug at once: retry with a random suffix.
            db.rollback()
            if attempt == 1:
                raise
            slug = unique_suffix(slug)
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
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")

    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="आप इस रचना को बदल नहीं सकते")

    update_data = post_data.model_dump(exclude_unset=True)
    if SELF_COVER_RE.search(update_data.get("cover_image") or ""):
        update_data.pop("cover_image")  # that is just our own served copy: keep what is stored
    if "title" in update_data and update_data["title"] != post.title:
        slug = generate_slug(update_data["title"])
        existing = db.query(Post).filter(Post.slug == slug, Post.id != post_id).first()
        if existing:
            slug = unique_suffix(slug)
        update_data["slug"] = slug

    new_tags = update_data.pop("tags", None)
    if "content_html" in update_data:
        update_data["content_html"] = clean_html(update_data["content_html"])
    for field, value in update_data.items():
        setattr(post, field, value)

    if new_tags is not None:
        post.tags = normalize_tags(new_tags, db)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="यह शीर्षक अभी उपयोग में है, कृपया दोबारा कोशिश करें")
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
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")

    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="आप इस रचना को हटा नहीं सकते")

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
    if not post or not can_view_post(post, current_user):
        raise HTTPException(status_code=404, detail="रचना नहीं मिली")
    enforce(actions, f"clap:{current_user.id}", *CLAP_LIMIT, record=True)

    mine = (Clap.user_id == current_user.id) & (Clap.post_id == post_id)
    # Atomic increments: concurrent taps can neither lose counts nor hit the
    # unique constraint with a 500.
    bumped = (
        db.query(Clap)
        .filter(mine, Clap.count < MAX_CLAPS_PER_POST)
        .update({Clap.count: Clap.count + 1}, synchronize_session=False)
    )
    if not bumped:
        existing = db.query(Clap).filter(mine).first()
        if existing is None:
            try:
                db.add(Clap(user_id=current_user.id, post_id=post_id, count=1))
                db.flush()
                bumped = 1
            except IntegrityError:
                db.rollback()
                bumped = (
                    db.query(Clap)
                    .filter(mine, Clap.count < MAX_CLAPS_PER_POST)
                    .update({Clap.count: Clap.count + 1}, synchronize_session=False)
                )
    if bumped:
        db.query(Post).filter(Post.id == post_id).update(
            {Post.clap_count: func.coalesce(Post.clap_count, 0) + 1, Post.updated_at: Post.updated_at},
            synchronize_session=False,
        )
    db.commit()
    clap = db.query(Clap).filter(mine).first()
    total = db.query(Post.clap_count).filter(Post.id == post_id).scalar()
    return ClapResponse(clap_count=total or 0, my_claps=clap.count if clap else 0)
