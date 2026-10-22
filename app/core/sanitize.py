"""Server-side HTML sanitization for rich-text article bodies.

The editor sends its rendered HTML; we store only a safe allowlisted subset
so the reading page can render it without executing attacker-controlled
markup. Structure mirrors what the editor can actually produce.
"""

from urllib.parse import urlparse

import bleach

ALLOWED_TAGS = [
    "p", "br", "hr", "div", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "strike", "sub", "sup", "mark",
    "a", "img",
    "blockquote", "ul", "ol", "li", "pre", "code",
    "iframe", "figure", "figcaption", "table", "thead", "tbody", "tr", "td", "th",
]

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

# Only these iframe hosts may be embedded (video embeds from the editor).
EMBED_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "player.vimeo.com")

# Permissions an embedded player may request; anything else is dropped.
SAFE_ALLOW_TOKENS = {
    "accelerometer", "autoplay", "clipboard-write", "encrypted-media",
    "fullscreen", "gyroscope", "picture-in-picture", "web-share",
}


def _allow_attr(tag, attr, value):
    if value is None:
        return False
    if attr == "class":
        # Editor state classes only (ql-*); nothing user-controlled matters.
        return all(token.startswith("ql-") for token in value.split())
    if attr == "style":
        # Only text alignment survives; everything else is stripped.
        return value.replace(" ", "").lower() in (
            "text-align:left;", "text-align:right;",
            "text-align:center;", "text-align:justify;",
        )
    if tag == "a" and attr == "href":
        return _href_ok(value)
    if tag == "a" and attr == "title":
        return True
    if tag == "img" and attr == "src":
        return _href_ok(value)
    if tag == "img" and attr in ("alt", "title"):
        return True
    if tag == "img" and attr in ("width", "height"):
        return value.isdigit() and len(value) <= 4
    if tag == "img" and attr == "loading":
        return value in ("lazy", "eager")
    if tag == "iframe":
        if attr in ("width", "height", "frameborder", "allowfullscreen", "title"):
            return True
        if attr == "allow":
            return all(
                token.strip() in SAFE_ALLOW_TOKENS
                for token in value.replace(",", ";").split(";")
                if token.strip()
            )
        if attr == "src":
            return _embed_ok(value)
    return False


def _href_ok(value):
    v = (value or "").strip().lower()
    if v.startswith(("//", "/\\")):  # protocol-relative: points at another site
        return False
    return v.startswith(("http://", "https://", "mailto:", "/"))


def _embed_ok(value):
    """https URL whose host is exactly an allowed host (or a subdomain of one)."""
    try:
        parsed = urlparse((value or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in EMBED_HOSTS)


ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height", "class", "loading"],
    "iframe": ["src", "width", "height", "frameborder", "allowfullscreen", "allow", "title"],
    "*": ["class", "style"],
}


def clean_html(raw):
    """Return sanitized article HTML, or None for empty input."""
    if not raw or not raw.strip():
        return None
    cleaned = bleach.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=_allow_attr,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    # Normalize a document that is only an empty paragraph to "no content".
    if cleaned.replace("<p>", "").replace("</p>", "").replace("<br>", "").strip() == "":
        return None
    return cleaned
