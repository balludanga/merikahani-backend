"""Server-rendered story pages for search and link-preview crawlers.

The React app is a client-side SPA, so a crawler that does not run (or is slow
to run) JavaScript would see an empty shell with the home page's tags.
vercel.json routes known crawler user agents on /post/:slug here instead; real
visitors keep getting the app. The page carries exactly what a signed-out
visitor sees - the teaser, not the whole story - so this is the same content,
not cloaking, and the structured data says the remainder is members-only.
"""

import json
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, selectinload

from app.api.endpoints.posts import cover_url
from app.core.config import settings
from app.core.content import PREVIEW_LIMIT, preview_text
from app.database import get_db
from app.models.post import Post

router = APIRouter()

# Crawlers refetch rarely; let Vercel's edge hold a page for an hour, and serve
# a stale copy for a day while it revalidates (also hides Render cold starts).
CACHE_HEADERS = {"Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400"}
RELATED_STORIES = 6

GATE_SELECTOR = ".story-gate"  # the sign-in wall; same class in the React page


def _json_ld(data: dict) -> str:
    """JSON for a <script> block: no sequence in it can close the tag."""
    text = json.dumps(data, ensure_ascii=False)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _iso(value) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S+00:00") if value else ""


def _attr(value: str) -> str:
    return escape(value or "", quote=True)


def _paragraphs(text: str) -> str:
    blocks = [block.strip() for block in (text or "").replace("\r", "").split("\n\n") if block.strip()]
    return "\n".join(f"<p>{escape(block).replace(chr(10), '<br>')}</p>" for block in blocks)


STYLE = """
body{margin:0;background:#FFFDF7;color:#1f2a28;font:18px/1.8 'Noto Serif Devanagari','Kohinoor Devanagari',Georgia,serif}
header,main,footer{max-width:720px;margin:0 auto;padding:16px 20px}
a{color:#0F766E} h1{font-size:2rem;line-height:1.45;margin:.4em 0}
.sub{color:#555;margin:0 0 .6em}.meta{color:#666;font-size:.9rem}
.story-gate{margin:32px 0;padding:20px;border:1px solid #d6e5e2;border-radius:12px;background:#f1f8f6}
.story-gate a{display:inline-block;margin:6px 10px 0 0;font-weight:600}
footer{font-size:.9rem;color:#666;border-top:1px solid #e6e1d6;margin-top:32px}
ul.related{padding-left:1.1em}
"""


def _not_found(base_url: str) -> HTMLResponse:
    html = (
        '<!doctype html><html lang="hi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow"><title>रचना नहीं मिली - लेखनी</title></head>'
        f'<body><h1>रचना नहीं मिली</h1><p><a href="{_attr(base_url)}/">लेखनी के मुखपृष्ठ पर जाएँ</a></p></body></html>'
    )
    return HTMLResponse(html, status_code=404, headers={"Cache-Control": "public, s-maxage=300"})


@router.get("/post/{slug}")
def prerender_post(slug: str, request: Request, db: Session = Depends(get_db)):
    base_url = settings.FRONTEND_URL.rstrip("/")
    post = (
        db.query(Post)
        .options(selectinload(Post.author))
        .filter(Post.slug == slug, Post.published == 1)
        .first()
    )
    if not post:
        return _not_found(base_url)

    content = (post.content or "").strip()
    gated = len(content) > PREVIEW_LIMIT  # same rule as the read gate for signed-out visitors
    author = post.author
    author_name = author.full_name or author.username
    story_url = f"{base_url}/post/{quote(post.slug)}"
    author_url = f"{base_url}/user/{author.id}"
    teaser = preview_text(content) if gated else content
    description = (post.subtitle or " ".join(teaser.split()))[:200]
    cover = post.cover_image or ""
    if cover.startswith("data:"):
        image = cover_url(request, post)
    elif cover.startswith(("http://", "https://")):
        image = cover
    else:
        image = f"{base_url}/logo.png"
    tags = [tag.name for tag in (post.tags or [])]
    title = post.title or "बिना शीर्षक"

    article = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title[:110],
        "description": description,
        "image": image,
        "inLanguage": "hi",
        "datePublished": _iso(post.created_at),
        "dateModified": _iso(post.updated_at),
        "mainEntityOfPage": {"@type": "WebPage", "@id": story_url},
        "author": {"@type": "Person", "name": author_name, "url": author_url},
        "publisher": {
            "@type": "Organization",
            "name": "लेखनी",
            "logo": {"@type": "ImageObject", "url": f"{base_url}/logo.png"},
        },
        "isAccessibleForFree": not gated,
    }
    if post.subtitle:
        article["alternativeHeadline"] = post.subtitle
    if tags:
        article["keywords"] = ", ".join(tags)
    if gated:
        # Tells search engines the rest sits behind sign-in (so the teaser is not cloaking).
        article["articleBody"] = teaser
        article["hasPart"] = {
            "@type": "WebPageElement",
            "isAccessibleForFree": False,
            "cssSelector": GATE_SELECTOR,
        }
    else:
        article["articleBody"] = content
    breadcrumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "लेखनी", "item": f"{base_url}/"},
            {"@type": "ListItem", "position": 2, "name": title, "item": story_url},
        ],
    }

    related = (
        db.query(Post)
        .options(selectinload(Post.author))
        .filter(Post.published == 1, Post.id != post.id)
        .order_by(Post.created_at.desc())
        .limit(RELATED_STORIES)
        .all()
    )

    if gated:
        body_html = _paragraphs(teaser)
    else:
        # Short stories are public in full; use the sanitized rich text when there is one.
        body_html = post.content_html or _paragraphs(content)

    tag_html = ""
    if tags:
        tag_html = "<p class=\"meta\">" + " · ".join(
            f'<a href="{_attr(base_url)}/tag/{_attr(quote(tag.slug))}">{escape(tag.name)}</a>' for tag in post.tags
        ) + "</p>"

    login = f"{base_url}/login?redirect={quote('/post/' + quote(post.slug), safe='')}"
    register = f"{base_url}/register?redirect={quote('/post/' + quote(post.slug), safe='')}"
    gate_html = ""
    if gated:
        gate_html = (
            '<div class="story-gate"><h2>पढ़ना जारी रखें</h2>'
            "<p>इस रचना को शुरू से अंत तक पढ़ने के लिए लेखनी से जुड़ें — पढ़ना और लिखना बिल्कुल मुफ़्त है।</p>"
            f'<a href="{_attr(register)}">मुफ़्त खाता बनाएँ</a><a href="{_attr(login)}">साइन इन करें</a></div>'
        )
    related_html = ""
    if related:
        items = "".join(
            f'<li><a href="{_attr(base_url)}/post/{_attr(quote(item.slug))}">{escape(item.title or "बिना शीर्षक")}</a>'
            f" — {escape(item.author.full_name or item.author.username)}</li>"
            for item in related
        )
        related_html = f"<h2>और रचनाएँ</h2><ul class=\"related\">{items}</ul>"

    # Pieces precomputed so the template below holds only plain names (Render runs
    # Python 3.11, which is stricter about quotes inside f-string expressions).
    tags_meta = "".join(f'<meta property="article:tag" content="{_attr(tag)}">' for tag in tags)
    subtitle_html = f'<p class="sub">{escape(post.subtitle)}</p>' if post.subtitle else ""
    date_text = post.created_at.strftime("%d-%m-%Y") if post.created_at else ""
    created_iso = _iso(post.created_at)
    modified_iso = _iso(post.updated_at)
    article_ld = _json_ld(article)
    breadcrumbs_ld = _json_ld(breadcrumbs)

    page = f"""<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} - लेखनी</title>
<meta name="description" content="{_attr(description)}">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="canonical" href="{_attr(story_url)}">
<link rel="icon" href="{_attr(base_url)}/favicon.ico">
<link rel="alternate" type="application/rss+xml" title="लेखनी" href="{_attr(base_url)}/rss.xml">
<meta property="og:site_name" content="लेखनी">
<meta property="og:type" content="article">
<meta property="og:locale" content="hi_IN">
<meta property="og:title" content="{_attr(title)}">
<meta property="og:description" content="{_attr(description)}">
<meta property="og:url" content="{_attr(story_url)}">
<meta property="og:image" content="{_attr(image)}">
<meta property="article:published_time" content="{created_iso}">
<meta property="article:modified_time" content="{modified_iso}">
<meta property="article:author" content="{_attr(author_name)}">
{tags_meta}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_attr(title)}">
<meta name="twitter:description" content="{_attr(description)}">
<meta name="twitter:image" content="{_attr(image)}">
<script type="application/ld+json">{article_ld}</script>
<script type="application/ld+json">{breadcrumbs_ld}</script>
<style>{STYLE}</style>
</head>
<body>
<header><a href="{_attr(base_url)}/">लेखनी</a> — हिंदी लेखकों का अपना मंच</header>
<main>
<article>
<h1>{escape(title)}</h1>
{subtitle_html}
<p class="meta"><a href="{_attr(author_url)}">{escape(author_name)}</a> · <time datetime="{created_iso}">{date_text}</time></p>
{tag_html}
<div class="story-body">
{body_html}
</div>
{gate_html}
</article>
{related_html}
</main>
<footer>
<a href="{_attr(base_url)}/about">परिचय</a> · <a href="{_attr(base_url)}/blog">ब्लॉग</a> ·
<a href="{_attr(base_url)}/help">सहायता</a> · <a href="{_attr(base_url)}/rules">नियम</a> ·
<a href="{_attr(base_url)}/privacy">गोपनीयता</a> · <a href="{_attr(base_url)}/terms">शर्तें</a>
</footer>
</body>
</html>"""
    return HTMLResponse(page, headers=CACHE_HEADERS)
