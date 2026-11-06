"""Write and earn: a writer applies, the review team decides, a partner reads their earnings.

The flow (modelled on a publication's partner programme: eligibility, then a person decides):

  1. A writer with enough followers and a published story applies (``POST /apply``). The server
     re-checks both numbers; the page only shows progress towards them.
  2. The review team (ADMIN_EMAILS, verified by Google) sees the application with signals about
     how real the followers look, and approves it or declines it with a written reason.
  3. The writer is told by the bell, as "the review team": the reviewer's own account is never
     named. A declined writer may apply again after PARTNER_REAPPLY_DAYS; a removed partner
     cannot, only the team can bring them back.
  4. A partner's stories earn points all month; the owner's side of the money lives in
     ``earnings.py``. A partner reads their own statements and balance here.

Everything for the review team answers everyone else with the same 404 a made-up address gets.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.admin import require_admin
from app.core.clock import utcnow
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.core.earnings import (
    CLAP_POINTS,
    HIGHLIGHT_POINTS,
    MAX_HIGHLIGHTS,
    MAX_RESPONSES,
    RESPONSE_POINTS,
    Tally,
    current_month,
    engagement,
    month_bounds,
    share_out,
    totals,
)
from app.core.notify import notify
from app.core.partner import (
    applicant_signals,
    blocker,
    follower_count,
    get_application,
    reapply_on,
    requirements,
    status_of,
    story_count,
    submit_application,
    tell_reviewers_of_application,
    top_stories,
)
from app.core.ratelimit import actions, enforce
from app.database import get_db
from app.models.follow import Follow
from app.models.notification import PARTNER_APPROVED, PARTNER_DECLINED, PARTNER_REVOKED
from app.models.partner import (
    APPROVED,
    DECLINED,
    PENDING,
    REVOKED,
    WITHDRAWN,
    EarningLine,
    EarningMonth,
    PartnerApplication,
    Payout,
)
from app.models.post import Post
from app.models.user import User
from app.schemas.partner import (
    ApplicationDetail,
    ApplicationOut,
    ApplyRequest,
    Decision,
    EarningsOut,
    LiveMonth,
    PartnerStatus,
    PayoutOut,
    QueueCounts,
    QueueItem,
    RequirementOut,
    RulesOut,
    Signals,
    StatementLine,
    TopStory,
    UpiRequest,
)

router = APIRouter()

NOT_FOUND = "आवेदन नहीं मिला"
PARTNERS_ONLY = "यह सुविधा साझेदार लेखकों के लिए है"
APPLY_LIMIT = (5, 24 * 60 * 60)  # applications a day: each one sends a notification to every reviewer
UPI_LIMIT = (10, 60 * 60)


# ---------------------------------------------------------------- the writer

def _rules() -> RulesOut:
    return RulesOut(
        min_followers=settings.PARTNER_MIN_FOLLOWERS,
        min_stories=settings.PARTNER_MIN_STORIES,
        clap_points=CLAP_POINTS,
        highlight_points=HIGHLIGHT_POINTS,
        max_highlights=MAX_HIGHLIGHTS,
        response_points=RESPONSE_POINTS,
        max_responses=MAX_RESPONSES,
        reader_min_age_days=settings.PARTNER_READER_MIN_AGE_DAYS,
        reapply_days=settings.PARTNER_REAPPLY_DAYS,
        min_payout_paise=settings.PARTNER_MIN_PAYOUT_RUPEES * 100,
    )


def _status(db: Session, user: User) -> PartnerStatus:
    application = get_application(db, user.id)
    followers, stories = requirements(db, user.id)
    reason = blocker(db, user.id, application)
    return PartnerStatus(
        status=status_of(application),
        followers=RequirementOut(have=followers.have, need=followers.need, met=followers.met),
        stories=RequirementOut(have=stories.have, need=stories.need, met=stories.met),
        can_apply=reason is None,
        blocked=reason,
        reapply_on=reapply_on(application),
        application=ApplicationOut.model_validate(application) if application else None,
        payout_upi=application.payout_upi if application else None,
        rules=_rules(),
    )


@router.get("/rules", response_model=RulesOut)
def programme_rules():
    """The numbers behind the programme. Public, so the page can quote them to a visitor who is
    not signed in; they are the same ones the server uses."""
    return _rules()


@router.get("/me", response_model=PartnerStatus)
def my_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Where the signed-in writer stands: what they have against what is asked, and the application."""
    return _status(db, current_user)


@router.post("/apply", response_model=PartnerStatus)
def apply(
    payload: ApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.agree:
        raise HTTPException(status_code=400, detail="आवेदन के लिए नियमों से सहमति ज़रूरी है")
    reason = blocker(db, current_user.id, get_application(db, current_user.id))
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    enforce(actions, f"partner-apply:{current_user.id}", *APPLY_LIMIT, record=True)
    submit_application(db, current_user, payload.note)
    tell_reviewers_of_application(db, current_user, payload.note)
    return _status(db, current_user)


@router.delete("/apply", response_model=PartnerStatus)
def withdraw(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Take a waiting application back."""
    application = get_application(db, current_user.id)
    if application is None or application.status != PENDING:
        raise HTTPException(status_code=400, detail="वापस लेने के लिए कोई लंबित आवेदन नहीं है")
    application.status = WITHDRAWN
    db.commit()
    return _status(db, current_user)


def _was_partner(application: PartnerApplication) -> bool:
    return application is not None and application.approved_at is not None


@router.put("/payout-account", response_model=PartnerStatus)
def set_payout_account(
    payload: UpiRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Where the owner sends the money. Only a partner (or a former one still owed) has one."""
    application = get_application(db, current_user.id)
    if not _was_partner(application):
        raise HTTPException(status_code=403, detail=PARTNERS_ONLY)
    enforce(actions, f"partner-upi:{current_user.id}", *UPI_LIMIT, record=True)
    application.payout_upi = payload.upi_id
    db.commit()
    return _status(db, current_user)


@router.get("/earnings", response_model=EarningsOut)
def my_earnings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The partner's balance, this month so far, every finalized month, and what has been paid."""
    application = get_application(db, current_user.id)
    if not _was_partner(application):
        raise HTTPException(status_code=403, detail=PARTNERS_ONLY)
    earned, paid = totals(db, current_user.id)

    lines = (
        db.query(EarningLine, EarningMonth.month)
        .join(EarningMonth, EarningMonth.id == EarningLine.month_id)
        .filter(EarningLine.user_id == current_user.id)
        .order_by(EarningMonth.month.desc())
        .limit(36)
        .all()
    )
    payouts = (
        db.query(Payout)
        .filter(Payout.user_id == current_user.id)
        .order_by(Payout.paid_at.desc(), Payout.id.desc())
        .limit(50)
        .all()
    )

    live = None
    if application.status == APPROVED:
        key = current_month()
        tallies = engagement(db, key)
        tally = tallies.get(current_user.id) or Tally(current_user.id)
        pool = db.query(EarningMonth).filter(EarningMonth.month == key).first()
        estimate = None
        if pool is not None and pool.finalized_at is None and pool.pool_paise > 0:
            shares = share_out({uid: t.points for uid, t in tallies.items()}, pool.pool_paise, pool.max_per_point_paise)
            estimate = shares.get(current_user.id, 0)
        live = LiveMonth(
            month=key,
            claps=tally.claps,
            highlights=tally.highlights,
            responses=tally.responses,
            readers=tally.readers,
            points=tally.points,
            estimate_paise=estimate,
            ends_at=month_bounds(key)[1],
        )

    return EarningsOut(
        balance_paise=earned - paid,
        earned_paise=earned,
        paid_paise=paid,
        live=live,
        months=[
            StatementLine(
                month=month,
                claps=line.claps,
                highlights=line.highlights,
                responses=line.responses,
                readers=line.readers,
                points=line.points,
                amount_paise=line.amount_paise,
                held_reason=line.held_reason,
            )
            for line, month in lines
        ],
        payouts=[PayoutOut.model_validate(p) for p in payouts],
    )


# ---------------------------------------------------------------- the review team

def _item_fields(application: PartnerApplication, followers: int, stories: int) -> dict:
    return dict(
        id=application.id,
        user=application.user,
        status=application.status,
        applied_at=application.applied_at,
        decided_at=application.decided_at,
        attempts=application.attempts or 1,
        followers_now=followers,
        followers_at_apply=application.followers_at_apply or 0,
        stories_now=stories,
        note=application.note,
    )


@router.get("/queue/counts", response_model=QueueCounts)
def queue_counts(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    def count(status: str) -> int:
        return db.query(func.count(PartnerApplication.id)).filter(PartnerApplication.status == status).scalar() or 0

    return QueueCounts(pending=count(PENDING), approved=count(APPROVED), declined=count(DECLINED), revoked=count(REVOKED))


@router.get("/queue", response_model=List[QueueItem])
def queue(
    view: str = Query("pending", pattern="^(pending|approved|declined|revoked)$"),
    q: str = Query("", max_length=60),
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Applications waiting for a decision (oldest first), or the ones already decided (newest first)."""
    query = (
        db.query(PartnerApplication)
        .options(selectinload(PartnerApplication.user))
        .join(User, User.id == PartnerApplication.user_id)
        .filter(PartnerApplication.status == view)
    )
    if q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        query = query.filter(User.username.ilike(like, escape="\\") | User.full_name.ilike(like, escape="\\"))
    if view == PENDING:
        query = query.order_by(PartnerApplication.applied_at.asc(), PartnerApplication.id.asc())
    else:
        query = query.order_by(PartnerApplication.decided_at.desc(), PartnerApplication.id.desc())
    rows = query.offset(skip).limit(limit).all()
    if not rows:
        return []

    ids = [row.user_id for row in rows]
    followers = dict(
        db.query(Follow.following_id, func.count(Follow.id))
        .filter(Follow.following_id.in_(ids))
        .group_by(Follow.following_id)
        .all()
    )
    stories = dict(
        db.query(Post.author_id, func.count(Post.id))
        .filter(Post.author_id.in_(ids), Post.published == 1)
        .group_by(Post.author_id)
        .all()
    )
    return [QueueItem(**_item_fields(row, followers.get(row.user_id, 0), stories.get(row.user_id, 0))) for row in rows]


def _application(db: Session, application_id: int) -> PartnerApplication:
    application = db.query(PartnerApplication).filter(PartnerApplication.id == application_id).first()
    if application is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return application


def _detail(db: Session, application: PartnerApplication) -> ApplicationDetail:
    user = application.user
    return ApplicationDetail(
        **_item_fields(application, follower_count(db, user.id), story_count(db, user.id)),
        decision_note=application.decision_note,
        approved_at=application.approved_at,
        bio=user.bio,
        tagline=user.tagline,
        member_since=user.created_at,
        payout_upi=application.payout_upi,
        signals=Signals(**applicant_signals(db, user)),
        top_stories=[TopStory(id=p.id, title=p.title, slug=p.slug, claps=p.clap_count or 0) for p in top_stories(db, user.id)],
    )


@router.get("/applications/{application_id}", response_model=ApplicationDetail)
def application_detail(
    application_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    return _detail(db, _application(db, application_id))


def _not_your_own(application: PartnerApplication, admin: User, message: str) -> None:
    """A reviewer never decides on their own application: that needs a second pair of eyes."""
    if application.user_id == admin.id:
        raise HTTPException(status_code=400, detail=message)


@router.post("/applications/{application_id}/approve", response_model=ApplicationDetail)
def approve(application_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Accept an application, or bring back a writer who was declined or removed."""
    application = _application(db, application_id)
    _not_your_own(application, admin, "अपना आवेदन आप स्वयं स्वीकृत नहीं कर सकते")
    if application.status not in (PENDING, DECLINED, REVOKED):
        raise HTTPException(status_code=400, detail="यह आवेदन अब स्वीकृत नहीं किया जा सकता")
    now = utcnow()
    application.status = APPROVED
    application.decided_at = now
    application.decided_by = admin.id
    application.decision_note = None
    application.approved_at = now
    db.commit()
    db.refresh(application)
    notify(db, actor_id=admin.id, kind=PARTNER_APPROVED, recipient_id=application.user_id)
    return _detail(db, application)


@router.post("/applications/{application_id}/decline", response_model=ApplicationDetail)
def decline(
    application_id: int,
    payload: Decision,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Say no, with a reason the writer will read. They may apply again after the waiting period."""
    application = _application(db, application_id)
    _not_your_own(application, admin, "अपना आवेदन आप स्वयं अस्वीकृत नहीं कर सकते")
    if application.status != PENDING:
        raise HTTPException(status_code=400, detail="यह आवेदन अब समीक्षा में नहीं है")
    application.status = DECLINED
    application.decided_at = utcnow()
    application.decided_by = admin.id
    application.decision_note = payload.reason
    db.commit()
    db.refresh(application)
    notify(db, actor_id=admin.id, kind=PARTNER_DECLINED, recipient_id=application.user_id, text=payload.reason)
    return _detail(db, application)


@router.post("/applications/{application_id}/revoke", response_model=ApplicationDetail)
def revoke(
    application_id: int,
    payload: Decision,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Stop a partner's earnings. Months already finalized (and what is still owed from them) stay
    theirs; anything not finalized yet, the month in progress included, is no longer counted."""
    application = _application(db, application_id)
    _not_your_own(application, admin, "अपनी साझेदारी आप स्वयं नहीं रोक सकते")
    if application.status != APPROVED:
        raise HTTPException(status_code=400, detail="केवल स्वीकृत साझेदार की साझेदारी रोकी जा सकती है")
    application.status = REVOKED
    application.decided_at = utcnow()
    application.decided_by = admin.id
    application.decision_note = payload.reason
    db.commit()
    db.refresh(application)
    notify(db, actor_id=admin.id, kind=PARTNER_REVOKED, recipient_id=application.user_id, text=payload.reason)
    return _detail(db, application)
