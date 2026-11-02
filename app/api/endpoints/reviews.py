"""Editorial review: a reviewer marks the passages that are a problem, sends the story back, and
publishes it again once the writer has fixed them.

The flow (modelled on a pull-request review rather than a one-comment-at-a-time chat, so the
writer gets one clear message instead of a trickle):

  1. A reviewer reads the story and adds notes on passages (``POST /post/{id}/notes``). Notes
     start as drafts that only reviewers can see.
  2. "Request changes" sends them all together: the story is taken down from the public (it is
     set to unpublished, so every list, feed and sitemap already omits it), the notes become
     visible to the writer, and the writer is notified.
  3. The writer edits, marks notes fixed, and sends the story back (the editor's publish button
     resubmits it; the story stays hidden).
  4. A reviewer approves it: it is published again at the same address.

Only reviewers (ADMIN_EMAILS, verified by Google) can review, and only stories that are live or
already under review: a private draft is never readable by a reviewer.
"""

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.admin import is_admin, require_admin
from app.core.clock import utcnow
from app.core.dependencies import get_current_user
from app.core.indexnow import notify as indexnow_notify, urls_for_story_change
from app.core.notify import notify
from app.core.ratelimit import actions, enforce
from app.core.review import tell_reviewers_of_resubmission
from app.database import get_db
from app.models.notification import REVIEW_APPROVED, REVIEW_CHANGES
from app.models.post import Post
from app.models.review import CHANGES_REQUESTED, CLEAR, FIXED, OPEN, RESUBMITTED, ReviewNote, ReviewRound
from app.models.user import User
from app.schemas.review import (
    NoteCreate,
    NoteResponse,
    NoteStatus,
    NoteUpdate,
    QueueCounts,
    QueueItem,
    RequestChanges,
    ReviewState,
    RoundResponse,
)

router = APIRouter()

MAX_NOTES_PER_POST = 100
NOT_FOUND = "रचना नहीं मिली"


def _reviewable_post(db: Session, post_id: int) -> Post:
    """A story a reviewer may open: live, or already under review. Never a private draft."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not (post.published == 1 or post.moderation_state != CLEAR):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return post


def _note_response(note: ReviewNote) -> NoteResponse:
    item = NoteResponse.model_validate(note)
    item.draft = note.round_id is None
    return item


def _reviewer_note(db: Session, note_id: int) -> ReviewNote:
    note = db.query(ReviewNote).filter(ReviewNote.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="टिप्पणी नहीं मिली")
    return note


def _story_urls(post: Post) -> List[str]:
    return urls_for_story_change(post.slug, post.author_id, [t.slug for t in post.tags])


# ---------------------------------------------------------------- the reviewer's list

@router.get("/queue/counts", response_model=QueueCounts)
def queue_counts(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    def count(state: str) -> int:
        return db.query(Post).filter(Post.moderation_state == state).count()

    return QueueCounts(resubmitted=count(RESUBMITTED), changes_requested=count(CHANGES_REQUESTED))


@router.get("/queue", response_model=List[QueueItem])
def queue(
    view: str = Query("resubmitted", pattern="^(resubmitted|changes_requested|live)$"),
    q: Optional[str] = Query(None, max_length=100),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Stories waiting for a reviewer (``resubmitted``), stories sent back (``changes_requested``),
    or the live stories, newest first, to pick one to read."""
    query = db.query(Post).options(selectinload(Post.author))
    if view == "live":
        query = query.filter(Post.published == 1)
    else:
        query = query.filter(Post.moderation_state == view)
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(Post.title.ilike(f"%{escaped}%", escape="\\"))
    order = Post.updated_at.asc() if view == "resubmitted" else Post.updated_at.desc()
    posts = query.order_by(order, Post.id.desc()).offset(skip).limit(limit).all()
    if not posts:
        return []

    ids = [post.id for post in posts]
    counts = {}
    for post_id, status, drafted, total in (
        db.query(ReviewNote.post_id, ReviewNote.status, ReviewNote.round_id.is_(None), func.count())
        .filter(ReviewNote.post_id.in_(ids))
        .group_by(ReviewNote.post_id, ReviewNote.status, ReviewNote.round_id.is_(None))
        .all()
    ):
        bucket = counts.setdefault(post_id, {"open": 0, "fixed": 0, "draft": 0})
        if drafted:
            bucket["draft"] += total
        else:
            bucket[status if status in ("open", "fixed") else "open"] += total
    latest = {}
    for round_ in db.query(ReviewRound).filter(ReviewRound.post_id.in_(ids)).order_by(ReviewRound.id.asc()).all():
        latest[round_.post_id] = round_.summary

    return [
        QueueItem(
            id=post.id,
            title=post.title,
            slug=post.slug,
            published=post.published,
            moderation_state=post.moderation_state,
            created_at=post.created_at,
            updated_at=post.updated_at,
            author=post.author,
            open_notes=counts.get(post.id, {}).get("open", 0),
            fixed_notes=counts.get(post.id, {}).get("fixed", 0),
            draft_notes=counts.get(post.id, {}).get("draft", 0),
            summary=latest.get(post.id),
        )
        for post in posts
    ]


# ---------------------------------------------------------------- one story's review

@router.get("/post/{post_id}", response_model=ReviewState)
def story_review(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The notes on a story. A reviewer sees all of them, drafts included; the story's writer
    sees only the ones that were sent. Everyone else gets a plain 404."""
    reviewer = is_admin(current_user)
    post = db.query(Post).filter(Post.id == post_id).first()
    is_writer = bool(post) and post.author_id == current_user.id
    if not post or not (is_writer or (reviewer and (post.published == 1 or post.moderation_state != CLEAR))):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    query = db.query(ReviewNote).filter(ReviewNote.post_id == post_id)
    if not reviewer:
        query = query.filter(ReviewNote.round_id.isnot(None))
    notes = query.order_by(ReviewNote.start_offset.asc().nullslast(), ReviewNote.id.asc()).all()
    rounds = db.query(ReviewRound).filter(ReviewRound.post_id == post_id).order_by(ReviewRound.id.asc()).all()
    return ReviewState(
        post=post,
        reviewer=reviewer,
        rounds=[RoundResponse.model_validate(r) for r in rounds],
        notes=[_note_response(n) for n in notes],
    )


@router.post("/post/{post_id}/notes", response_model=NoteResponse, status_code=201)
def add_note(
    post_id: int,
    payload: NoteCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    post = _reviewable_post(db, post_id)
    enforce(actions, f"review-note:{admin.id}", 200, 10 * 60, record=True)
    if db.query(ReviewNote).filter(ReviewNote.post_id == post.id).count() >= MAX_NOTES_PER_POST:
        raise HTTPException(status_code=400, detail=f"एक रचना पर {MAX_NOTES_PER_POST} से अधिक टिप्पणियाँ नहीं हो सकतीं")
    note = ReviewNote(
        post_id=post.id,
        reviewer_id=admin.id,
        text=payload.text,
        start_offset=payload.start,
        prefix=payload.prefix,
        suffix=payload.suffix,
        category=payload.category,
        comment=payload.comment,
        suggestion=payload.suggestion,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _note_response(note)


@router.put("/notes/{note_id}", response_model=NoteResponse)
def edit_note(
    note_id: int,
    payload: NoteUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    note = _reviewer_note(db, note_id)
    _reviewable_post(db, note.post_id)
    changes = payload.model_dump(exclude_unset=True)
    if "category" in changes and changes["category"]:
        note.category = changes["category"]
    if "comment" in changes and changes["comment"]:
        note.comment = changes["comment"]
    if "suggestion" in changes:
        note.suggestion = changes["suggestion"] or None
    db.commit()
    db.refresh(note)
    return _note_response(note)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    note = _reviewer_note(db, note_id)
    db.delete(note)
    db.commit()
    return None


@router.put("/notes/{note_id}/status", response_model=NoteResponse)
def set_note_status(
    note_id: int,
    payload: NoteStatus,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The writer ticks a note off as fixed (or back to open); a reviewer can reopen one."""
    note = _reviewer_note(db, note_id)
    post = db.query(Post).filter(Post.id == note.post_id).first()
    is_writer = bool(post) and post.author_id == current_user.id
    if not post or not (is_writer or is_admin(current_user)):
        raise HTTPException(status_code=404, detail="टिप्पणी नहीं मिली")
    if note.round_id is None and not is_admin(current_user):
        # A draft note has not been sent: the writer does not know it exists.
        raise HTTPException(status_code=404, detail="टिप्पणी नहीं मिली")
    note.status = FIXED if payload.status == "fixed" else OPEN
    db.commit()
    db.refresh(note)
    return _note_response(note)


# ---------------------------------------------------------------- decisions

@router.post("/post/{post_id}/request-changes", response_model=ReviewState)
def request_changes(
    post_id: int,
    payload: RequestChanges,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Send the story back: take it down, show the writer every drafted note, tell them."""
    post = _reviewable_post(db, post_id)
    drafts = db.query(ReviewNote).filter(ReviewNote.post_id == post.id, ReviewNote.round_id.is_(None)).all()
    if not drafts and not payload.summary:
        raise HTTPException(status_code=400, detail="कम से कम एक टिप्पणी जोड़ें, या लेखक के लिए दो पंक्तियाँ लिखें")

    round_ = ReviewRound(post_id=post.id, reviewer_id=admin.id, summary=payload.summary)
    db.add(round_)
    db.flush()
    for note in drafts:
        note.round_id = round_.id
    was_live = post.published == 1
    post.published = 0
    post.moderation_state = CHANGES_REQUESTED
    db.commit()
    db.refresh(post)

    notify(
        db,
        actor_id=admin.id,
        kind=REVIEW_CHANGES,
        post=post,
        text=payload.summary or (drafts[0].comment if drafts else None),
        count=len(drafts),
    )
    if was_live:
        # Gone from the public: tell search engines the address no longer serves the story.
        background_tasks.add_task(indexnow_notify, _story_urls(post))
    return story_review(post.id, db, admin)


@router.post("/post/{post_id}/approve", response_model=ReviewState)
def approve(
    post_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Publish the story again, at the same address."""
    post = _reviewable_post(db, post_id)
    if post.moderation_state == CLEAR:
        raise HTTPException(status_code=400, detail="इस रचना पर कोई समीक्षा लंबित नहीं है")
    post.published = 1
    post.moderation_state = CLEAR
    now = utcnow()
    for round_ in db.query(ReviewRound).filter(ReviewRound.post_id == post.id, ReviewRound.approved_at.is_(None)).all():
        round_.approved_at = now
    db.commit()
    db.refresh(post)
    notify(db, actor_id=admin.id, kind=REVIEW_APPROVED, post=post, text=post.title)
    background_tasks.add_task(indexnow_notify, _story_urls(post))
    return story_review(post.id, db, admin)


@router.post("/post/{post_id}/resubmit", response_model=ReviewState)
def resubmit(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The writer sends the corrected story back to the reviewers."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or post.author_id != current_user.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if post.moderation_state == RESUBMITTED:
        return story_review(post.id, db, current_user)
    if post.moderation_state != CHANGES_REQUESTED:
        raise HTTPException(status_code=400, detail="इस रचना में बदलाव नहीं माँगे गए हैं")
    post.moderation_state = RESUBMITTED
    db.commit()
    db.refresh(post)
    tell_reviewers_of_resubmission(db, post, current_user)
    return story_review(post.id, db, current_user)
