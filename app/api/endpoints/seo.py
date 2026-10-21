from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from datetime import datetime
from app.database import get_db
from app.models.post import Post
from app.models.user import User
from app.core.config import settings

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
    xml += f'    <loc>{base_url}/</loc>\n'
    xml += f'    <lastmod>{datetime.utcnow().strftime("%Y-%m-%d")}</lastmod>\n'
    xml += '    <changefreq>daily</changefreq>\n'
    xml += '    <priority>1.0</priority>\n'
    xml += '  </url>\n'

    # Get all published posts
    posts = db.query(Post).filter(Post.published == 1).order_by(Post.created_at.desc()).all()

    for post in posts:
        xml += '  <url>\n'
        xml += f'    <loc>{base_url}/post/{post.slug}</loc>\n'
        xml += f'    <lastmod>{post.updated_at.strftime("%Y-%m-%d")}</lastmod>\n'
        xml += '    <changefreq>weekly</changefreq>\n'
        xml += '    <priority>0.8</priority>\n'
        xml += '  </url>\n'

    # Get all users with posts
    users = db.query(User).join(Post).filter(Post.published == 1).distinct().all()

    for user in users:
        xml += '  <url>\n'
        xml += f'    <loc>{base_url}/user/{user.id}</loc>\n'
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
    rss += '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
    rss += '  <channel>\n'
    rss += '    <title>लेखनी (Lekhani) — हिंदी लेखकों का अपना मंच</title>\n'
    rss += f'    <link>{base_url}</link>\n'
    rss += '    <description>हिंदी में कहानी, कविता, निबंध और संस्मरण — मानवीय लेखन का घर</description>\n'
    rss += '    <language>hi</language>\n'
    rss += f'    <lastBuildDate>{datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>\n'
    rss += f'    <atom:link href="{base_url}/rss.xml" rel="self" type="application/rss+xml"/>\n'

    # Get latest 50 published posts
    posts = db.query(Post).filter(Post.published == 1).order_by(Post.created_at.desc()).limit(50).all()

    for post in posts:
        rss += '    <item>\n'
        rss += f'      <title>{post.title}</title>\n'
        rss += f'      <link>{base_url}/post/{post.slug}</link>\n'
        rss += f'      <description>{post.subtitle or post.content[:200]}</description>\n'
        rss += f'      <author>{post.author.email} ({post.author.full_name or post.author.username})</author>\n'
        rss += f'      <pubDate>{post.created_at.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate>\n'
        rss += f'      <guid>{base_url}/post/{post.slug}</guid>\n'
        rss += '    </item>\n'

    rss += '  </channel>\n'
    rss += '</rss>'

    return Response(content=rss, media_type="application/xml")
