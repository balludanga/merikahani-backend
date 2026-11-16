"""Deleting an account.

What goes: the person's stories (with every clap, highlight, response, bookmark, narration and review
note on them), their own responses, claps, highlights, bookmarks and follows on other people's pages,
their notifications, their profile pictures and every picture they uploaded, and the personal details on
the account itself.

What stays, on purpose: the records of money (payments, payouts, the books, each month's lines). A site
that has taken payments has to keep them for its accounts and for the tax office. They are not left
readable: the account row they point to becomes an empty shell (no name, no email, no sign-in), a free-text
message a supporter attached to a payment is cleared, and the writer's UPI id is removed.

It is refused while the site owes the writer money (it would be lost), while a membership is set to renew
(it would go on charging someone who cannot sign in), and for the owner's own account.
"""

import logging
import secrets
from typing import List, Optional

import requests
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.core.earnings import balance_paise
from app.core.indexnow import urls_for_story_change
from app.core.ledger import OWED
from app.core.security import get_password_hash
from app.models.bookmark import Bookmark
from app.models.clap import Clap
from app.models.comment import Comment
from app.models.follow import Follow
from app.models.highlight import Highlight
from app.models.ledger import LedgerEntry
from app.models.notification import Notification
from app.models.partner import APPROVED, REVOKED, PartnerApplication
from app.models.payments import ACTIVE, Membership, Payment
from app.models.post import Post
from app.models.user import User
from app.models.user_media import UserMedia

logger = logging.getLogger(__name__)


def blocker(db: Session, user: User) -> Optional[str]:
    """Why this account cannot be deleted right now (a sentence for the person), or None."""
    if user.is_owner:
        return "यह मालिक का खाता है, इसे यहाँ से हटाया नहीं जा सकता"
    owed = balance_paise(db, user.id)
    # The books keep what is owed as a credit (a negative sum), so flip the sign.
    in_books = -int(
        db.query(func.coalesce(func.sum(LedgerEntry.amount_paise), 0))
        .filter(LedgerEntry.account == OWED, LedgerEntry.user_id == user.id)
        .scalar()
    )
    if owed > 0 or in_books > 0:
        return "आपका कुछ पैसा बकाया है। खाता हटाने से वह चला जाएगा, इसलिए पहले उसे ले लीजिए"
    renewing = (
        db.query(Membership.id)
        .filter(Membership.user_id == user.id, Membership.status == ACTIVE, Membership.auto_renew.is_(True))
        .first()
    )
    if renewing:
        return "आपकी सदस्यता अपने आप नवीनीकृत होती है। खाता हटाने से पहले उसे रद्द कर दीजिए"
    return None


def erase(db: Session, user: User) -> List[str]:
    """Delete the person's content and turn the account into an empty shell, in one transaction.

    Returns the public addresses that changed, for the search engines. The caller has checked ``blocker``.
    """
    uid = user.id
    urls: List[str] = []

    # Their stories, with everything that hangs on them (the models cascade).
    post_ids = []
    for post in db.query(Post).filter(Post.author_id == uid).all():
        if post.published == 1:
            urls += urls_for_story_change(post.slug, uid, [t.slug for t in post.tags])
        post_ids.append(post.id)
        db.delete(post)
    if post_ids:
        # A supporter's payment record stays but no longer points at a story that is gone.
        db.query(Payment).filter(Payment.post_id.in_(post_ids)).update({Payment.post_id: None}, synchronize_session=False)
    db.flush()

    # Their responses elsewhere (an answer under one goes with it, as when a response is deleted by hand).
    mine = db.query(Comment).filter(Comment.author_id == uid).all()
    mine_ids = {comment.id for comment in mine}
    for comment in mine:
        if comment.parent_id in mine_ids:
            continue  # goes with the response it answers
        gone = [comment.id] + [reply.id for reply in comment.replies]
        db.query(Notification).filter(Notification.comment_id.in_(gone)).delete(synchronize_session=False)
        db.delete(comment)

    # Claps given elsewhere: take the taps back off the stories' counts (without touching their edit time).
    for post_id, taps in db.query(Clap.post_id, Clap.count).filter(Clap.user_id == uid).all():
        db.query(Post).filter(Post.id == post_id).update(
            {
                Post.clap_count: case((Post.clap_count > taps, Post.clap_count - taps), else_=0),
                Post.updated_at: Post.updated_at,
            },
            synchronize_session=False,
        )
    db.query(Clap).filter(Clap.user_id == uid).delete(synchronize_session=False)
    db.query(Highlight).filter(Highlight.user_id == uid).delete(synchronize_session=False)
    db.query(Bookmark).filter(Bookmark.user_id == uid).delete(synchronize_session=False)
    db.query(Follow).filter(or_(Follow.follower_id == uid, Follow.following_id == uid)).delete(synchronize_session=False)
    db.query(Notification).filter(or_(Notification.user_id == uid, Notification.actor_id == uid)).delete(synchronize_session=False)
    db.query(UserMedia).filter(UserMedia.user_id == uid).delete(synchronize_session=False)

    # Personal details inside the records that stay.
    db.query(PartnerApplication).filter(PartnerApplication.user_id == uid).update(
        {PartnerApplication.payout_upi: None, PartnerApplication.note: None, PartnerApplication.decision_note: None},
        synchronize_session=False,
    )
    # No longer a partner: their stories are gone, and nothing is owed (checked above).
    db.query(PartnerApplication).filter(PartnerApplication.user_id == uid, PartnerApplication.status == APPROVED).update(
        {PartnerApplication.status: REVOKED}, synchronize_session=False
    )
    db.query(Payment).filter(or_(Payment.user_id == uid, Payment.target_user_id == uid)).update(
        {Payment.message: None}, synchronize_session=False
    )

    # The account itself: nothing that names a person, no way in, and every token already issued is dead.
    user.email = f"deleted-{uid}@deleted.invalid"
    user.username = f"deleted_{uid}"
    for field in (
        "full_name", "bio", "avatar_url", "tagline", "status_line", "location", "accent",
        "cover_style", "cover_url", "genres_json", "links_json", "pinned_json",
    ):
        setattr(user, field, None)
    user.hashed_password = get_password_hash(secrets.token_urlsafe(48))
    user.auth_provider = "deleted"
    user.email_verified = False
    user.token_version = (user.token_version or 0) + 1
    user.deleted_at = utcnow()
    db.commit()
    return urls


def remove_uploaded_files(user_id: int) -> None:
    """Delete every picture this person uploaded to the media bucket (best effort: it runs after the
    account is gone, and a failure is logged, never raised). Uploads live under media/<user id>/."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        return
    base = settings.SUPABASE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}", "Content-Type": "application/json"}
    prefix = f"media/{user_id}"
    try:
        for _ in range(20):  # at most 20 rounds of 1000 files
            listed = requests.post(
                f"{base}/storage/v1/object/list/{settings.UPLOAD_BUCKET}",
                json={"prefix": prefix, "limit": 1000, "offset": 0},
                headers=headers,
                timeout=20,
            )
            listed.raise_for_status()
            names = [item["name"] for item in listed.json() if item.get("name")]
            if not names:
                return
            removed = requests.delete(
                f"{base}/storage/v1/object/{settings.UPLOAD_BUCKET}",
                json={"prefixes": [f"{prefix}/{name}" for name in names]},
                headers=headers,
                timeout=20,
            )
            removed.raise_for_status()
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        logger.warning("Could not remove the uploaded files of deleted account %s: %s", user_id, error)
