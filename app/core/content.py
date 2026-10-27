"""Publication-style read gate: signed-out visitors get a teaser, members read everything."""

from typing import Optional

from app.models.post import Post
from app.models.user import User

# Signed-out visitors can read this many characters before the gate appears.
PREVIEW_LIMIT = 600


def can_view_post(post: Post, user: Optional[User]) -> bool:
    """Published posts are public; drafts are visible only to their author."""
    return post.published == 1 or (user is not None and post.author_id == user.id)


def preview_text(content: str, limit: int = PREVIEW_LIMIT) -> str:
    """Trim content to a teaser that ends cleanly at a paragraph boundary."""
    if not content:
        return content
    cut = content[:limit]
    # Back off to the last paragraph break (or at least a space) so the
    # teaser never stops mid-word or mid-sentence.
    for separator in ("\n\n", "\n", " "):
        idx = cut.rfind(separator)
        if idx > limit * 0.5:
            cut = cut[:idx]
            break
    return cut.rstrip() + " …"


def apply_read_gate(item, user: Optional[User]):
    """Trim one response object's body for a signed-out reader.

    Callers pass the *copy* built for the response, never the ORM row: a gated read
    that rewrote ``post.content`` on a session-bound instance would leave the teaser
    sitting there, and any later commit in that request (a notification, a counter,
    a retry) would store it as the story — silently destroying what the writer wrote.

    Sets ``item.is_preview``: ``True`` when content was cut for a signed-out reader,
    ``False`` otherwise. Content at or below ``PREVIEW_LIMIT`` characters stays fully
    readable — there is nothing meaningful left to hide behind the gate.
    """
    content = item.content or ""
    if user is None and len(content) > PREVIEW_LIMIT:
        item.content = preview_text(content)
        # Never leak the full body through the rich-text rendering.
        item.content_html = None
        item.is_preview = True
    else:
        item.is_preview = False
    return item
