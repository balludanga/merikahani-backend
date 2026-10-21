"""Publication-style read gate: signed-out visitors get a teaser, members read everything."""

from typing import List, Optional, Union

from app.models.post import Post
from app.models.user import User

# Signed-out visitors can read this many characters before the gate appears.
PREVIEW_LIMIT = 600


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


def apply_read_gate(posts: Union[Post, List[Post]], user: Optional[User]) -> None:
    """Truncate content for anonymous viewers and flag posts as previews.

    Sets ``post.is_preview`` on every post: ``True`` when content was cut
    for a signed-out reader, ``False`` otherwise. Content at or below
    ``PREVIEW_LIMIT`` characters stays fully readable — there is nothing
    meaningful left to hide behind the gate.
    """
    if isinstance(posts, Post):
        posts = [posts]
    for post in posts:
        content = post.content or ""
        if user is None and len(content) > PREVIEW_LIMIT:
            post.content = preview_text(content)
            # Never leak the full body through the rich-text rendering.
            post.content_html = None
            post.is_preview = True
        else:
            post.is_preview = False
