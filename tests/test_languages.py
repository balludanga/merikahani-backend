"""Stories in the 22 scheduled languages and English, and the languages a person reads and writes in."""

from unittest.mock import patch

import pytest

from app.core import languages as langs

# The same 23 codes, in the same order, are pinned in the frontend (src/languages/registry.test.js).
EXPECTED_CODES = [
    "hi", "en", "as", "bn", "brx", "doi", "gu", "kn", "ks", "kok", "mai", "ml", "mni", "mr", "ne", "or", "pa",
    "sa", "sat", "sd", "ta", "te", "ur",
]

# One real sentence in each language's own script (what a writer would paste or type).
SAMPLES = {
    "hi": "यह एक कहानी है।",
    "en": "This is a story.",
    "as": "এইটো এটা সাধু।",
    "bn": "এটি একটি গল্প।",
    "brx": "बे मिथिं बिदा।",
    "doi": "एह् इक कहानी ऐ।",
    "gu": "આ એક વાર્તા છે.",
    "kn": "ಇದು ಒಂದು ಕಥೆ.",
    "ks": "یہٕ چھُ اکھ کتھ۔",
    "kok": "ही एक कथा आसा.",
    "mai": "ई एकटा कथा अछि।",
    "ml": "ഇത് ഒരു കഥയാണ്.",
    "mni": "ꯃꯁꯤ ꯂꯥꯛꯄ ꯃꯤꯑꯣꯏꯅꯤ꯫",
    "mr": "ही एक गोष्ट आहे.",
    "ne": "यो एउटा कथा हो।",
    "or": "ଏହା ଏକ ଗଳ୍ପ।",
    "pa": "ਇਹ ਇੱਕ ਕਹਾਣੀ ਹੈ।",
    "sa": "इयं कथा अस्ति।",
    "sat": "ᱱᱚᱶᱟ ᱢᱤᱫ ᱠᱷᱚᱵᱚᱨ ᱾",
    "sd": "هي هڪ ڪهاڻي آهي.",
    "ta": "இது ஒரு கதை.",
    "te": "ఇది ఒక కథ.",
    "ur": "یہ ایک کہانی ہے۔",
}


def _story(client, who, title, language=None, published=1, content=None):
    body = {"title": title, "content": content or "यह एक परीक्षण रचना है। " * 3, "published": published}
    if language is not None:
        body["language"] = language
    return client.post("/api/posts", json=body, headers=who["headers"])


# ---------------------------------------------------------------- the registry

def test_the_registry_lists_exactly_the_22_scheduled_languages_and_english():
    assert list(langs.CODES) == EXPECTED_CODES
    assert len(langs.CODES) == 23
    assert langs.DEFAULT_LANGUAGE == "hi"
    for language in langs.LANGUAGES:
        assert language.hindi and language.native and language.english
        assert language.script in {"Deva", "Beng", "Gujr", "Guru", "Knda", "Mlym", "Orya", "Taml", "Telu", "Arab", "Olck", "Mtei", "Latn"}


def test_only_the_perso_arabic_languages_run_right_to_left():
    rtl = {language.code for language in langs.LANGUAGES if language.rtl}
    assert rtl == {"ks", "sd", "ur"}


def test_codes_are_cleaned_the_way_people_write_them():
    assert langs.normalize_language("TA") == "ta"
    assert langs.normalize_language(" ta-IN ") == "ta"
    assert langs.normalize_language("hin") == "hi"
    assert langs.normalize_language("ks_Arab") == "ks"
    for bad in ("", None, "xx", "french", "zh"):
        with pytest.raises(ValueError):
            langs.normalize_language(bad)


def test_a_feed_filter_keeps_known_codes_in_order_without_repeats():
    assert langs.parse_filter(None) is None
    assert langs.parse_filter("  ") is None
    assert langs.parse_filter("ta, hi,ta,zz") == ["ta", "hi"]
    assert langs.parse_filter("zz,qq") == []


def test_each_language_has_a_sentence_end_and_a_locale():
    assert langs.BY_CODE["hi"].stop == "।"
    assert langs.BY_CODE["ur"].stop == "۔"
    assert langs.BY_CODE["ta"].stop == "."
    assert langs.og_locale("ta") == "ta_IN"
    assert langs.og_locale("nonsense") == "hi_IN"
    assert langs.html_lang("nonsense") == "hi"


def test_the_letter_pattern_covers_every_script_but_not_its_punctuation():
    import re

    word = re.compile("[" + langs.LETTER_RANGES + r"\w]+")
    for code, text in SAMPLES.items():
        first = text.split()[0].rstrip("।.॥۔꯫᱾")
        assert word.fullmatch(first), (code, first)
    for stop in "।॥۔꯫᱾":
        assert not word.fullmatch(stop), stop


def test_the_languages_list_endpoint_matches_the_registry(test_client):
    # not strictly needed by the page (it carries its own copy) but handy for tools
    response = test_client.get("/api/languages")
    assert response.status_code == 200
    assert [row["code"] for row in response.json()] == EXPECTED_CODES
    ta = next(row for row in response.json() if row["code"] == "ta")
    assert ta["native"] == "தமிழ்" and ta["rtl"] is False


# ---------------------------------------------------------------- stories carry a language

def test_a_story_without_a_language_is_hindi(test_client, writer_a):
    post = _story(test_client, writer_a, "बिना भाषा बताए").json()
    assert post["language"] == "hi"


@pytest.mark.parametrize("code", EXPECTED_CODES)
def test_a_story_can_be_written_in_every_supported_language(test_client, writer_a, code):
    response = _story(test_client, writer_a, f"{code} रचना", language=code, content=SAMPLES[code] * 6)
    assert response.status_code == 201, response.text
    post = response.json()
    assert post["language"] == code
    again = test_client.get(f"/api/posts/{post['id']}").json()
    assert again["language"] == code
    assert again["content"].startswith(SAMPLES[code])


def test_an_unknown_language_is_refused_with_a_hindi_message(test_client, writer_a):
    response = _story(test_client, writer_a, "अनजान भाषा", language="klingon")
    assert response.status_code == 422
    assert "भाषा" in response.text


def test_the_language_can_be_corrected_later_and_is_never_null(test_client, writer_a):
    post = _story(test_client, writer_a, "भाषा सुधारें", language="hi").json()
    changed = test_client.put(f"/api/posts/{post['id']}", json={"language": "mr"}, headers=writer_a["headers"])
    assert changed.status_code == 200 and changed.json()["language"] == "mr"
    # leaving it out keeps it
    kept = test_client.put(f"/api/posts/{post['id']}", json={"subtitle": "नया"}, headers=writer_a["headers"])
    assert kept.json()["language"] == "mr"
    # null and rubbish are refused
    assert test_client.put(f"/api/posts/{post['id']}", json={"language": None}, headers=writer_a["headers"]).status_code == 422
    assert test_client.put(f"/api/posts/{post['id']}", json={"language": "zz"}, headers=writer_a["headers"]).status_code == 422


def test_the_story_address_keeps_the_letters_of_its_own_script(test_client, writer_a):
    titles = {
        "ta": "தமிழ் கதை",
        "bn": "বাংলা গল্প",
        "ml": "മലയാളം കഥ",
        "ur": "اردو کہانی",
        "sat": "ᱥᱟᱱᱛᱟᱲᱤ ᱠᱷᱚᱵᱚᱨ",
        "mni": "ꯃꯅꯤꯄꯨꯔꯤ ꯋꯥꯍꯩ",
    }
    for code, title in titles.items():
        post = _story(test_client, writer_a, title, language=code).json()
        assert post["slug"] and not post["slug"].startswith("lekh-"), (code, post["slug"])
        assert test_client.get(f"/api/posts/slug/{post['slug']}").json()["title"] == title


# ---------------------------------------------------------------- the feed by language

def test_the_feed_can_be_limited_to_the_chosen_languages(test_client, writer_b):
    ta = _story(test_client, writer_b, "तमिल फ़ीड", language="ta").json()
    bn = _story(test_client, writer_b, "बांग्ला फ़ीड", language="bn").json()
    hi = _story(test_client, writer_b, "हिंदी फ़ीड").json()

    def ids(**params):
        rows = test_client.get("/api/posts", params={"limit": 100, **params}).json()
        return {row["id"] for row in rows}

    everything = ids()
    assert {ta["id"], bn["id"], hi["id"]} <= everything
    only_ta = ids(lang="ta")
    assert ta["id"] in only_ta and bn["id"] not in only_ta and hi["id"] not in only_ta
    two = ids(lang="ta,bn")
    assert {ta["id"], bn["id"]} <= two and hi["id"] not in two
    # codes written loosely still work; nothing valid means an empty feed, not everything
    assert ta["id"] in ids(lang="TA-in")
    assert ids(lang="zz") == set()
    # the languages are shown on every story
    row = next(r for r in test_client.get("/api/posts", params={"lang": "bn"}).json() if r["id"] == bn["id"])
    assert row["language"] == "bn"


def test_a_draft_in_any_language_stays_private(test_client, writer_a):
    draft = _story(test_client, writer_a, "तेलुगु मसौदा", language="te", published=0).json()
    rows = test_client.get("/api/posts", params={"lang": "te", "limit": 100}).json()
    assert draft["id"] not in {row["id"] for row in rows}


# ---------------------------------------------------------------- what a person reads and writes in

def test_a_writer_chooses_the_languages_they_read_and_write(test_client, writer_a):
    me = test_client.get("/api/auth/me", headers=writer_a["headers"]).json()
    assert me["read_languages"] == [] and me["write_languages"] == []

    response = test_client.put(
        "/api/users/me/profile",
        json={"read_languages": ["ta", "hi", "ta", "ENG"], "write_languages": ["ta"]},
        headers=writer_a["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_languages"] == ["ta", "hi", "en"]
    assert body["write_languages"] == ["ta"]

    me = test_client.get("/api/auth/me", headers=writer_a["headers"]).json()
    assert me["read_languages"] == ["ta", "hi", "en"] and me["write_languages"] == ["ta"]


def test_what_a_person_reads_in_is_private_what_they_write_in_is_public(test_client, writer_a, writer_b):
    test_client.put(
        "/api/users/me/profile",
        json={"read_languages": ["ur"], "write_languages": ["ur", "hi"]},
        headers=writer_a["headers"],
    )
    page = test_client.get(f"/api/users/{writer_a['user']['id']}", headers=writer_b["headers"]).json()
    assert page["write_languages"] == ["ur", "hi"]
    assert page["read_languages"] is None
    anonymous = test_client.get(f"/api/users/{writer_a['user']['id']}").json()
    assert anonymous["read_languages"] is None
    own = test_client.get(f"/api/users/{writer_a['user']['id']}", headers=writer_a["headers"]).json()
    assert own["read_languages"] == ["ur"]


def test_an_unknown_language_in_the_profile_is_refused_and_an_empty_list_clears(test_client, writer_a):
    bad = test_client.put("/api/users/me/profile", json={"read_languages": ["xx"]}, headers=writer_a["headers"])
    assert bad.status_code == 422
    cleared = test_client.put(
        "/api/users/me/profile", json={"read_languages": [], "write_languages": []}, headers=writer_a["headers"]
    )
    assert cleared.status_code == 200
    assert cleared.json()["read_languages"] == [] and cleared.json()["write_languages"] == []


# ---------------------------------------------------------------- what search engines and feeds are told

def test_the_page_for_search_engines_names_the_story_language(test_client, writer_b):
    post = _story(test_client, writer_b, "தமிழ் கதை பக்கம்", language="ta", content=SAMPLES["ta"] * 5).json()
    html = test_client.get(f"/api/prerender/post/{post['slug']}").text
    assert '<html lang="ta">' in html
    assert '"inLanguage": "ta"' in html or '"inLanguage":"ta"' in html
    assert 'og:locale" content="ta_IN"' in html
    assert 'dir="rtl"' not in html


def test_a_right_to_left_story_page_says_so(test_client, writer_b):
    post = _story(test_client, writer_b, "اردو کہانی صفحہ", language="ur", content=SAMPLES["ur"] * 5).json()
    html = test_client.get(f"/api/prerender/post/{post['slug']}").text
    assert '<html lang="ur" dir="rtl">' in html
    share = test_client.get(f"/api/share/post/{post['slug']}").text
    assert 'lang="ur" dir="rtl"' in share and 'og:locale" content="ur_PK"' in share


def test_hindi_story_pages_are_unchanged(test_client, writer_b):
    post = _story(test_client, writer_b, "हिंदी कहानी पृष्ठ").json()
    html = test_client.get(f"/api/prerender/post/{post['slug']}").text
    assert '<html lang="hi">' in html and 'og:locale" content="hi_IN"' in html


def test_the_feed_reader_can_pick_languages_and_each_item_names_its_language(test_client, writer_b):
    post = _story(test_client, writer_b, "ગુજરાતી આરએસએસ", language="gu", content=SAMPLES["gu"] * 5).json()
    everything = test_client.get("/api/rss.xml").text
    assert "<dc:language>gu</dc:language>" in everything
    only = test_client.get("/api/rss.xml", params={"lang": "gu"}).text
    assert post["slug"] in only and "<dc:language>hi</dc:language>" not in only
    none = test_client.get("/api/rss.xml", params={"lang": "zz"}).text
    assert "<item>" not in none


# ---------------------------------------------------------------- narration is only for the languages set up for it

def test_the_server_voice_reads_the_languages_google_lists_and_leaves_the_rest_to_the_device(test_client, writer_b):
    hi = _story(test_client, writer_b, "आवाज़ हिंदी").json()
    ta = _story(test_client, writer_b, "குரல் தமிழ்", language="ta", content=SAMPLES["ta"] * 6).json()
    sa = _story(test_client, writer_b, "ध्वनिः संस्कृतम्", language="sa", content=SAMPLES["sa"] * 6).json()
    with patch("app.api.endpoints.audio.is_available", return_value=True):
        assert test_client.get("/api/audio/status", params={"post_id": hi["id"]}).json()["enabled"] is True
        assert test_client.get("/api/audio/status", params={"post_id": ta["id"]}).json()["enabled"] is True
        # Google lists no Sanskrit voice: the listener's own device reads it
        assert test_client.get("/api/audio/status", params={"post_id": sa["id"]}).json()["enabled"] is False
        with patch("app.api.endpoints.audio.settings.TTS_LANGUAGES", "hi"):  # an owner who turns the rest off
            assert test_client.get("/api/audio/status", params={"post_id": ta["id"]}).json()["enabled"] is False
        # asking for it anyway never starts a job for a language that is off
        assert test_client.get(f"/api/audio/post/{sa['id']}").status_code == 503


# ---------------------------------------------------------------- the language a person prefers

def test_a_person_can_name_one_preferred_language(test_client, writer_c):
    me = test_client.get("/api/auth/me", headers=writer_c["headers"]).json()
    assert me["primary_language"] is None

    response = test_client.put(
        "/api/users/me/profile",
        json={"read_languages": ["hi", "ta"], "primary_language": "TA"},
        headers=writer_c["headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["primary_language"] == "ta"
    me = test_client.get("/api/auth/me", headers=writer_c["headers"]).json()
    assert me["primary_language"] == "ta" and me["read_languages"] == ["hi", "ta"]


def test_the_preferred_language_is_always_one_they_read(test_client, writer_c):
    # naming a language they do not read adds it, first
    done = test_client.put(
        "/api/users/me/profile", json={"read_languages": ["hi"], "primary_language": "bn"}, headers=writer_c["headers"]
    ).json()
    assert done["primary_language"] == "bn" and done["read_languages"] == ["bn", "hi"]
    # taking it off the list without naming another clears it
    done = test_client.put("/api/users/me/profile", json={"read_languages": ["hi"]}, headers=writer_c["headers"]).json()
    assert done["primary_language"] is None and done["read_languages"] == ["hi"]
    # with no reading list at all a preferred language is allowed (every language is shown, this one first)
    done = test_client.put(
        "/api/users/me/profile", json={"read_languages": [], "primary_language": "mr"}, headers=writer_c["headers"]
    ).json()
    assert done["primary_language"] == "mr" and done["read_languages"] == []


def test_an_unknown_preferred_language_is_refused_and_an_empty_string_clears(test_client, writer_c):
    assert test_client.put(
        "/api/users/me/profile", json={"primary_language": "klingon"}, headers=writer_c["headers"]
    ).status_code == 422
    cleared = test_client.put("/api/users/me/profile", json={"primary_language": ""}, headers=writer_c["headers"])
    assert cleared.status_code == 200 and cleared.json()["primary_language"] is None


def test_the_preferred_language_is_private(test_client, writer_a, writer_c):
    test_client.put("/api/users/me/profile", json={"primary_language": "ta"}, headers=writer_c["headers"])
    seen = test_client.get(f"/api/users/{writer_c['user']['id']}", headers=writer_a["headers"]).json()
    assert seen["primary_language"] is None
    own = test_client.get(f"/api/users/{writer_c['user']['id']}", headers=writer_c["headers"]).json()
    assert own["primary_language"] == "ta"


def test_the_feed_puts_the_preferred_language_first(test_client, writer_b):
    bn = _story(test_client, writer_b, "पहले बांग्ला", language="bn").json()
    hi = _story(test_client, writer_b, "बाद में हिंदी").json()  # newer than the Bengali one
    ta = _story(test_client, writer_b, "बाद में तमिल", language="ta").json()  # newest

    def order(**params):
        rows = test_client.get("/api/posts", params={"limit": 100, **params}).json()
        return [row["id"] for row in rows if row["id"] in (bn["id"], hi["id"], ta["id"])]

    assert order() == [ta["id"], hi["id"], bn["id"]]  # newest first, as before
    assert order(primary="bn") == [bn["id"], ta["id"], hi["id"]]  # Bengali first, the rest newest first
    assert order(primary="ta", lang="ta,bn") == [ta["id"], bn["id"]]
    assert order(primary="zz") == [ta["id"], hi["id"], bn["id"]]  # not a language: no preference


# ---------------------------------------------------------------- every list of stories follows the reader's languages

def test_a_tag_page_follows_the_reader_languages_too(test_client, writer_b):
    def tagged(title, language):
        body = {"title": title, "content": "टैग वाली रचना। " * 5, "language": language, "published": 1, "tags": ["बहुभाषी-टैग"]}
        return test_client.post("/api/posts", json=body, headers=writer_b["headers"]).json()

    hi = tagged("टैग हिंदी", "hi")
    ta = tagged("டேக் தமிழ்", "ta")
    bn = tagged("ট্যাগ বাংলা", "bn")
    slug = test_client.get("/api/posts/%d" % hi["id"]).json()["tag_slugs"][0]

    def ids(**params):
        return [row["id"] for row in test_client.get(f"/api/tags/{slug}/posts", params=params).json()]

    assert set(ids()) == {hi["id"], ta["id"], bn["id"]}
    assert ids(lang="ta") == [ta["id"]]
    assert set(ids(lang="ta,bn")) == {ta["id"], bn["id"]}
    assert ids(lang="zz") == []
    assert ids(primary="hi")[0] == hi["id"]
    assert ids(primary="bn", lang="bn,ta") == [bn["id"], ta["id"]]
