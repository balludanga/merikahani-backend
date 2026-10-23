"""What a writer may put on their profile, and the rules for it.

Everything here is plain text shown by React (never as HTML), so the rules are
about length, shape and safe links rather than escaping.
"""

import re
import unicodedata
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# Banner designs the browser knows how to draw (frontend/src/utils/profileStyle.js).
COVER_STYLES = ["dawn", "sky", "forest", "ember", "ink", "sand", "twilight", "meadow"]

# Kinds of writing a writer can say they do. A fixed list keeps the chips tidy.
GENRES = [
    "कहानी", "लघुकथा", "कविता", "ग़ज़ल", "गीत", "निबंध", "संस्मरण", "व्यंग्य",
    "उपन्यास", "यात्रा-वृत्तांत", "समीक्षा", "नाटक", "आत्मकथा", "बाल साहित्य",
]
MAX_GENRES = 5
MAX_LINKS = 5
MAX_PINS = 3

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clean(value: Optional[str], multiline: bool = False) -> Optional[str]:
    """Trim, drop control characters, collapse spaces; empty becomes None."""
    if value is None:
        return None
    kept = "".join(
        ch for ch in value
        if ch == "\n" or ch == "\t" or unicodedata.category(ch) != "Cc"
    )
    if multiline:
        lines = [" ".join(line.split()) for line in kept.replace("\r", "").split("\n")]
        text = "\n".join(lines).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = " ".join(kept.split())
    return text or None


class LinkItem(BaseModel):
    label: Optional[str] = Field(None, max_length=60)
    url: str = Field(max_length=300)

    @field_validator("label")
    @classmethod
    def tidy_label(cls, value):
        label = _clean(value)
        if label and len(label) > 30:
            raise ValueError("लिंक का नाम 30 अक्षरों तक रखें")
        return label

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        url = value.strip()
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in ("http", "https") or "." not in host or "@" in parsed.netloc or " " in url:
            raise ValueError("लिंक http:// या https:// से शुरू होने वाला सही पता होना चाहिए")
        return url


class ProfileUpdate(BaseModel):
    """Send only what changed. An empty string clears a field."""

    full_name: Optional[str] = Field(None, max_length=100)
    tagline: Optional[str] = Field(None, max_length=200)
    status_line: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=120)
    bio: Optional[str] = Field(None, max_length=2000)
    accent: Optional[str] = None
    cover_style: Optional[str] = None
    genres: Optional[List[str]] = None
    links: Optional[List[LinkItem]] = None
    pinned_post_ids: Optional[List[int]] = None

    @field_validator("full_name", "tagline", "status_line", "location")
    @classmethod
    def one_line(cls, value, info):
        limits = {"full_name": 60, "tagline": 100, "status_line": 100, "location": 60}
        text = _clean(value)
        if text and len(text) > limits[info.field_name]:
            raise ValueError(f"अधिकतम {limits[info.field_name]} अक्षर रखें")
        return text

    @field_validator("bio")
    @classmethod
    def bio_text(cls, value):
        text = _clean(value, multiline=True)
        if text and len(text) > 600:
            raise ValueError("परिचय अधिकतम 600 अक्षर का हो सकता है")
        return text

    @field_validator("accent")
    @classmethod
    def accent_hex(cls, value):
        if value in (None, ""):
            return None
        if not _HEX.match(value):
            raise ValueError("रंग #rrggbb रूप में होना चाहिए")
        return value.lower()

    @field_validator("cover_style")
    @classmethod
    def known_cover(cls, value):
        if value in (None, ""):
            return None
        if value not in COVER_STYLES:
            raise ValueError("यह बैनर डिज़ाइन उपलब्ध नहीं है")
        return value

    @field_validator("genres")
    @classmethod
    def known_genres(cls, value):
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        if any(g not in GENRES for g in unique):
            raise ValueError("यह विधा सूची में नहीं है")
        if len(unique) > MAX_GENRES:
            raise ValueError(f"अधिकतम {MAX_GENRES} विधाएँ चुनें")
        return unique

    @field_validator("links")
    @classmethod
    def few_links(cls, value):
        if value is None:
            return None
        if len(value) > MAX_LINKS:
            raise ValueError(f"अधिकतम {MAX_LINKS} लिंक जोड़ें")
        seen, out = set(), []
        for item in value:
            if item.url not in seen:
                seen.add(item.url)
                out.append(item)
        return out

    @field_validator("pinned_post_ids")
    @classmethod
    def few_pins(cls, value):
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        if len(unique) > MAX_PINS:
            raise ValueError(f"अधिकतम {MAX_PINS} रचनाएँ चुनी जा सकती हैं")
        return unique
