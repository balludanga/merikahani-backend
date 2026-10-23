import json
import re
import uuid
from urllib.parse import quote


def _ld_blocks(html):
    return [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S)]


def _story(client, headers, **fields):
    body = {"title": f"कहानी {uuid.uuid4().hex[:6]}", "content": "छोटी रचना।", "published": 1, **fields}
    response = client.post("/api/posts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_long_story_page_shows_only_the_teaser_and_marks_the_paywall(test_client, writer_a):
    marker = uuid.uuid4().hex
    paragraphs = "\n\n".join(f"अनुच्छेद {i} " + "शब्द " * 30 for i in range(30))
    story = _story(test_client, writer_a["headers"], content=paragraphs + marker, subtitle="उपशीर्षक", tags=["परीक्षा"])
    page = test_client.get(f"/api/prerender/post/{story['slug']}")
    assert page.status_code == 200
    html = page.text
    assert marker not in html  # the members-only remainder never reaches the crawler
    assert "<h1>" in html and story["title"] in html
    assert 'class="story-gate"' in html
    assert f'rel="canonical" href="https://lekhanii.vercel.app/post/' in html
    assert 'name="description" content="उपशीर्षक"' in html
    assert "s-maxage" in page.headers["cache-control"]
    article = next(b for b in _ld_blocks(html) if b["@type"] == "BlogPosting")
    assert article["isAccessibleForFree"] is False
    assert article["hasPart"]["cssSelector"] == ".story-gate" and article["hasPart"]["isAccessibleForFree"] is False
    assert marker not in article["articleBody"]
    assert article["author"]["name"] and article["inLanguage"] == "hi"
    assert next(b for b in _ld_blocks(html) if b["@type"] == "BreadcrumbList")["itemListElement"][1]["name"] == story["title"]
    assert "परीक्षा" in article["keywords"]


def test_short_story_page_is_public_in_full(test_client, writer_a):
    story = _story(test_client, writer_a["headers"], content="पूरी रचना यहीं है।")
    html = test_client.get(f"/api/prerender/post/{story['slug']}").text
    assert "पूरी रचना यहीं है।" in html
    assert 'class="story-gate"' not in html
    article = next(b for b in _ld_blocks(html) if b["@type"] == "BlogPosting")
    assert article["isAccessibleForFree"] is True and "hasPart" not in article


def test_story_page_links_to_other_stories_for_crawling(test_client, writer_a):
    other = _story(test_client, writer_a["headers"])
    story = _story(test_client, writer_a["headers"])
    html = test_client.get(f"/api/prerender/post/{story['slug']}").text
    assert "और रचनाएँ" in html
    assert f"/post/{quote(other['slug'])}" in html


def test_missing_and_draft_stories_are_real_404s(test_client, writer_a):
    draft = _story(test_client, writer_a["headers"], published=0)
    for slug in (draft["slug"], "no-such-story-at-all"):
        page = test_client.get(f"/api/prerender/post/{slug}")
        assert page.status_code == 404
        assert "noindex" in page.text


def test_hostile_title_cannot_break_out_of_html_or_json_ld(test_client, writer_a):
    evil = '</script><script>alert(1)</script> "x" & <b>'
    story = _story(test_client, writer_a["headers"], title=evil, subtitle="</script><img src=x onerror=alert(1)>")
    html = test_client.get(f"/api/prerender/post/{story['slug']}").text
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    article = next(b for b in _ld_blocks(html) if b["@type"] == "BlogPosting")  # still valid JSON
    assert article["headline"] == evil


def test_tag_page_lists_its_published_stories_only(test_client, writer_a):
    tag = f"टैग{uuid.uuid4().hex[:5]}"
    mine = _story(test_client, writer_a["headers"], tags=[tag])
    hidden = _story(test_client, writer_a["headers"], tags=[tag], published=0)
    listing = test_client.get("/api/tags").json()
    slug = next((t["slug"] for t in listing if t["name"] == tag), None)
    assert slug, "tag should appear once it has a published story"
    page = test_client.get(f"/api/prerender/tag/{slug}")
    assert page.status_code == 200
    assert mine["title"] in page.text and hidden["title"] not in page.text
    assert f"/post/{quote(mine['slug'])}" in page.text and "<h1>" in page.text
    assert 'rel="canonical" href="https://lekhanii.vercel.app/tag/' in page.text
    blocks = _ld_blocks(page.text)
    assert next(b for b in blocks if b["@type"] == "CollectionPage")["mainEntity"]["itemListElement"]
    assert test_client.get("/api/prerender/tag/nonexistent-tag").status_code == 404


def test_author_page_lists_stories_without_leaking_email_or_drafts(test_client, writer_a):
    story = _story(test_client, writer_a["headers"])
    draft = _story(test_client, writer_a["headers"], published=0)
    page = test_client.get(f"/api/prerender/user/{writer_a['user']['id']}")
    assert page.status_code == 200
    assert story["title"] in page.text and draft["title"] not in page.text
    assert writer_a["user"]["email"] not in page.text
    profile = next(b for b in _ld_blocks(page.text) if b["@type"] == "ProfilePage")
    assert profile["mainEntity"]["@type"] == "Person"
    assert test_client.get("/api/prerender/user/99999999").status_code == 404


def test_author_with_no_published_story_is_noindex(test_client):
    import uuid as _uuid
    body = {"email": f"empty_{_uuid.uuid4().hex[:8]}@example.com", "username": f"empty_{_uuid.uuid4().hex[:8]}", "password": "secret123"}
    user = test_client.post("/api/auth/register", json=body).json()
    page = test_client.get(f"/api/prerender/user/{user['id']}")
    assert page.status_code == 200 and "noindex" in page.text


def test_sitemap_lists_stories_authors_and_tags_encoded(test_client, writer_a):
    import xml.etree.ElementTree as ET

    tag = f"साइटमैप{uuid.uuid4().hex[:5]}"
    story = _story(test_client, writer_a["headers"], tags=[tag])
    root = ET.fromstring(test_client.get("/api/sitemap.xml").text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text for e in root.findall("s:url/s:loc", ns)]
    assert f"https://lekhanii.vercel.app/post/{quote(story['slug'])}" in locs
    assert any("/user/" in loc for loc in locs) and any("/tag/" in loc for loc in locs)
    assert all(loc.isascii() for loc in locs)  # percent-encoded
    assert "https://lekhanii.vercel.app/" not in locs  # static pages live in the frontend sitemap
    assert all(e.find("s:lastmod", ns) is not None for e in root.findall("s:url", ns))
