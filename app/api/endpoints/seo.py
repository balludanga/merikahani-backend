from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime
from urllib.parse import quote
from urllib.parse import quote
from xml.sax.saxutils import escape
from app.database import get_db
from app.models.post import Post
from app.models.user import User
from app.core.config import settings
from app.api.endpoints.posts import cover_url
from app.core.content import preview_text

# Sitemap protocol limit is 50,000 URLs per file (home page included).
MAX_SITEMAP_URLS = 49000
from app.api.endpoints.posts import cover_url
from fastapi import Request

router = APIRouter()

@router.get("/sitemap.xml")
def generate_sitemap(db: Session = Depends(get_db)):
    """Generate XML sitemap for SEO"""

    base_url = settings.FRONTEND_URL.rstrip("/")

    # Start XML
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    # Homepage
    xml += '  <url>\n'
    xml += f'    <loc>{escape(base_url)}/</loc>\n'
    xml += f'    <lastmod>{datetime.utcnow().strftime("%Y-%m-%d")}</lastmod>\n'
    xml += '    <changefreq>daily</changefreq>\n'
    xml += '    <priority>1.0</priority>\n'
    xml += '  </url>\n'

    # Get all published posts
    posts = (
        db.query(Post)
        .filter(Post.published == 1)
        .order_by(Post.created_at.desc())
        .limit(MAX_SITEMAP_URLS)
        .all()
    )

    for post in posts:
        xml += '  <url>\n'
        xml += f'    <loc>{escape(base_url)}/post/{escape(quote(post.slug))}</loc>\n'
        xml += f'    <lastmod>{post.updated_at.strftime("%Y-%m-%d")}</lastmod>\n'
        xml += '    <changefreq>weekly</changefreq>\n'
        xml += '    <priority>0.8</priority>\n'
        xml += '  </url>\n'

    # Get all users with posts
    users = (
        db.query(User.id, func.max(Post.updated_at))
        .join(Post, Post.author_id == User.id)
        .filter(Post.published == 1)
        .group_by(User.id)
        .limit(MAX_SITEMAP_URLS)
        .all()
    )

    for user_id, last_post_update in users:
        xml += '  <url>\n'
        xml += f'    <loc>{escape(base_url)}/user/{user_id}</loc>\n'
        if last_post_update:
            xml += f'    <lastmod>{last_post_update.strftime("%Y-%m-%d")}</lastmod>\n'
        xml += '    <changefreq>weekly</changefreq>\n'
        xml += '    <priority>0.6</priority>\n'
        xml += '  </url>\n'

    xml += '</urlset>'

    return Response(content=xml, media_type="application/xml")

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
    rss += f'    <lastBuildDate>{datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>\n'
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
