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
