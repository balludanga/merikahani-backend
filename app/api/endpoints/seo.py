from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.endpoints.posts import cover_url
from app.core.clock import utcnow
from app.core.config import settings
from app.core.content import preview_text
from app.database import get_db
from app.models.post import Post
from app.models.tag import Tag, post_tags
from app.models.user import User

# Sitemap protocol limit is 50,000 URLs per file. Static pages (home, about, blog guides)
# live in the frontend's own sitemap-pages.xml, so this file holds only database-driven URLs.
MAX_STORY_URLS = 45000
MAX_HUB_URLS = 2500  # author pages, and tag pages, each

router = APIRouter()


def _url_entry(loc: str, lastmod) -> str:
    entry = f"  <url>\n    <loc>{escape(loc)}</loc>\n"
    if lastmod:
        entry += f"    <lastmod>{lastmod.strftime('%Y-%m-%d')}</lastmod>\n"
    return entry + "  </url>\n"


@router.get("/sitemap.xml")
def generate_sitemap(db: Session = Depends(get_db)):
    """Stories, author pages and tag pages (published content only)."""
    base_url = settings.FRONTEND_URL.rstrip("/")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    stories = (
        db.query(Post.slug, Post.updated_at)
        .filter(Post.published == 1)
        .order_by(Post.created_at.desc())
        .limit(MAX_STORY_URLS)
        .all()
    )
    for slug, updated_at in stories:
        xml += _url_entry(f"{base_url}/post/{quote(slug)}", updated_at)

    authors = (
        db.query(User.id, func.max(Post.updated_at))
        .join(Post, Post.author_id == User.id)
        .filter(Post.published == 1)
        .group_by(User.id)
        .limit(MAX_HUB_URLS)
        .all()
    )
    for user_id, last_update in authors:
        xml += _url_entry(f"{base_url}/user/{user_id}", last_update)

    tags = (
        db.query(Tag.slug, func.max(Post.updated_at))
        .join(post_tags, post_tags.c.tag_id == Tag.id)
        .join(Post, Post.id == post_tags.c.post_id)
        .filter(Post.published == 1)
        .group_by(Tag.id)
        .limit(MAX_HUB_URLS)
        .all()
    )
    for slug, last_update in tags:
        xml += _url_entry(f"{base_url}/tag/{quote(slug)}", last_update)

    return Response(content=xml + "</urlset>", media_type="application/xml")


@router.get("/rss.xml")
def generate_rss(db: Session = Depends(get_db)):
    """Generate RSS feed for published posts"""

    base_url = settings.FRONTEND_URL.rstrip("/")

    # Start RSS
    rss = '<?xml version="1.0" encoding="UTF-8"?>\n'
    rss += '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
    rss += '  <channel>\n'
    rss += '    <title>लेखनी — हिंदी लेखकों का अपना मंच</title>\n'
    rss += f'    <link>{base_url}</link>\n'
    rss += '    <description>हिंदी में कहानी, कविता, निबंध और संस्मरण — मानवीय लेखन का घर</description>\n'
    rss += '    <language>hi</language>\n'
    rss += f'    <lastBuildDate>{utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>\n'
    rss += f'    <atom:link href="{base_url}/rss.xml" rel="self" type="application/rss+xml"/>\n'

    # Get latest 50 published posts
    posts = db.query(Post).filter(Post.published == 1).order_by(Post.created_at.desc()).limit(50).all()

    for post in posts:
        rss += '    <item>\n'
        rss += f'      <title>{escape(post.title or "")}</title>\n'
        rss += f'      <link>{escape(base_url)}/post/{escape(post.slug)}</link>\n'
        rss += f'      <description>{escape(post.subtitle or (post.content or "")[:200])}</description>\n'
        rss += f'      <dc:creator>{escape(post.author.full_name or post.author.username)}</dc:creator>\n'
        rss += f'      <pubDate>{post.created_at.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate>\n'
        rss += f'      <guid>{escape(base_url)}/post/{escape(post.slug)}</guid>\n'
        rss += '    </item>\n'

    rss += '  </channel>\n'
    rss += '</rss>'

    return Response(content=rss, media_type="application/xml")


@router.get("/share/post/{slug}")
def share_post(slug: str, request: Request, db: Session = Depends(get_db)):
    """Tiny page for link-preview crawlers (they don't run the React app).

    Carries the story's own title/description/image as Open Graph tags and
    sends real visitors straight on to the story page.
    """
    base_url = settings.FRONTEND_URL.rstrip("/")
    post = db.query(Post).filter(Post.slug == slug, Post.published == 1).first()
    if not post:
        return HTMLResponse(
            '<!doctype html><html lang="hi"><meta charset="utf-8"><title>रचना नहीं मिली</title>'
            f'<meta http-equiv="refresh" content="0;url={escape(base_url)}/">',
            status_code=404,
        )
    target = f"{base_url}/post/{quote(post.slug)}"
    description = (post.subtitle or preview_text((post.content or "").strip(), 160)).replace("\n", " ")
    image = post.cover_image or ""
    if image.startswith("data:"):
        image = cover_url(request, post)
    elif not image.startswith(("http://", "https://")):
        image = f"{base_url}/logo.png"
    author = post.author.full_name or post.author.username

    def attr(value: str) -> str:
        return escape(value, {'"': "&quot;"})

    html = f"""<!doctype html>
<html lang="hi"><head><meta charset="utf-8">
<title>{escape(post.title)}</title>
<meta name="description" content="{attr(description)}">
<link rel="canonical" href="{attr(target)}">
<meta property="og:type" content="article">
<meta property="og:title" content="{attr(post.title)}">
<meta property="og:description" content="{attr(description)}">
<meta property="og:url" content="{attr(target)}">
<meta property="og:image" content="{attr(image)}">
<meta property="og:locale" content="hi_IN">
<meta property="article:author" content="{attr(author)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{attr(post.title)}">
<meta name="twitter:description" content="{attr(description)}">
<meta name="twitter:image" content="{attr(image)}">
<meta http-equiv="refresh" content="0;url={attr(target)}">
</head><body><p><a href="{attr(target)}">{escape(post.title)}</a></p></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=300"})
