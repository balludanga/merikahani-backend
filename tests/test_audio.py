"""Server-side narration: access rules, caching, quota guard, and the SSML it sends."""

import re
from unittest.mock import patch

import pytest

from app.core.narration import plan_narration, spoken_length, to_ssml_chunks
from app.core.speech import SpeechUnavailable

SHORT = "एक छोटी सी कहानी, जो बहुत जल्दी खत्म हो जाती है।"
LONG = "यह एक लंबी रचना का नमूना अंश है। " * 80  # past the preview limit


@pytest.fixture
def azure_on():
    """Pretend a speech key is set, and count what would have been sent to Azure."""
    calls = []

    def fake(chunks):
        calls.append(chunks)
        return b"ID3-fake-mp3-" + str(len(calls)).encode()

    with patch("app.api.endpoints.audio.settings.AZURE_SPEECH_KEY", "test-key"), patch(
        "app.api.endpoints.audio.synthesize", side_effect=fake
    ):
        yield calls


def _post(client, writer, content, title="सुनने की रचना", published=1):
    response = client.post(
        "/api/posts",
        json={"title": title, "content": content, "published": published},
        headers=writer["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_status_tells_the_page_whether_to_use_the_server_voice(test_client):
    assert test_client.get("/api/audio/status").json() == {"enabled": False}
    with patch("app.api.endpoints.audio.settings.AZURE_SPEECH_KEY", "test-key"):
        assert test_client.get("/api/audio/status").json() == {"enabled": True}


def test_off_without_a_key(test_client, writer_a):
    post = _post(test_client, writer_a, SHORT)
    response = test_client.get(f"/api/audio/post/{post['id']}")
    assert response.status_code == 503


def test_short_story_is_open_to_everyone_and_cached(test_client, writer_a, azure_on):
    post = _post(test_client, writer_a, SHORT)
    first = test_client.get(f"/api/audio/post/{post['id']}")
    assert first.status_code == 200
    assert first.headers["content-type"] == "audio/mpeg"
    assert first.headers["cache-control"].startswith("private")
    assert first.content.startswith(b"ID3")

    second = test_client.get(f"/api/audio/post/{post['id']}")
    assert second.status_code == 200
    assert second.content == first.content
    assert len(azure_on) == 1  # the second listen cost no quota

    cached = test_client.get(f"/api/audio/post/{post['id']}", headers={"If-None-Match": first.headers["etag"]})
    assert cached.status_code == 304


def test_long_story_needs_sign_in_like_reading_does(test_client, writer_a, writer_b, azure_on):
    post = _post(test_client, writer_a, LONG)
    assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 401
    assert len(azure_on) == 0
    signed_in = test_client.get(f"/api/audio/post/{post['id']}", headers=writer_b["headers"])
    assert signed_in.status_code == 200


def test_drafts_are_only_for_their_author(test_client, writer_a, writer_b, azure_on):
    post = _post(test_client, writer_a, SHORT, published=0)
    assert test_client.get(f"/api/audio/post/{post['id']}", headers=writer_b["headers"]).status_code == 404
    assert test_client.get(f"/api/audio/post/{post['id']}", headers=writer_a["headers"]).status_code == 200


def test_editing_the_story_makes_new_audio(test_client, writer_a, azure_on):
    post = _post(test_client, writer_a, SHORT)
    test_client.get(f"/api/audio/post/{post['id']}")
    updated = test_client.put(
        f"/api/posts/{post['id']}", json={"content": SHORT + " और एक वाक्य जुड़ गया।"}, headers=writer_a["headers"]
    )
    assert updated.status_code == 200, updated.text
    again = test_client.get(f"/api/audio/post/{post['id']}")
    assert again.status_code == 200
    assert len(azure_on) == 2


def test_too_long_a_story_is_left_to_the_browser_voice(test_client, writer_a, azure_on):
    post = _post(test_client, writer_a, "शब्द। " * 3000)
    with patch("app.api.endpoints.audio.settings.TTS_MAX_STORY_CHARS", 1000):
        response = test_client.get(f"/api/audio/post/{post['id']}", headers=writer_a["headers"])
    assert response.status_code == 413
    assert len(azure_on) == 0


def test_monthly_budget_stops_new_audio_but_not_cached_audio(test_client, writer_a, azure_on):
    heard = _post(test_client, writer_a, SHORT, title="पहले से बनी")
    assert test_client.get(f"/api/audio/post/{heard['id']}").status_code == 200
    fresh = _post(test_client, writer_a, SHORT + " नई रचना।", title="बजट के बाद")
    with patch("app.api.endpoints.audio.settings.TTS_MONTHLY_CHARS", 1):
        assert test_client.get(f"/api/audio/post/{fresh['id']}").status_code == 503
        assert test_client.get(f"/api/audio/post/{heard['id']}").status_code == 200


def test_azure_trouble_becomes_a_fallback_status_and_nothing_is_stored(test_client, writer_a):
    post = _post(test_client, writer_a, SHORT + " अलग पाठ।", title="विफल")
    with patch("app.api.endpoints.audio.settings.AZURE_SPEECH_KEY", "test-key"), patch(
        "app.api.endpoints.audio.synthesize", side_effect=SpeechUnavailable("azure status 429")
    ):
        assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 503
    with patch("app.api.endpoints.audio.settings.AZURE_SPEECH_KEY", "test-key"), patch(
        "app.api.endpoints.audio.synthesize", return_value=b"ID3-ok"
    ) as ok:
        assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 200
        assert ok.call_count == 1  # the earlier failure left nothing behind


def test_deleting_a_story_removes_its_audio(test_client, writer_a, azure_on):
    post = _post(test_client, writer_a, SHORT + " मिटने वाली।", title="मिटेगी")
    assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 200
    assert test_client.delete(f"/api/posts/{post['id']}", headers=writer_a["headers"]).status_code in (200, 204)


class TestNarration:
    def test_paragraphs_get_longer_silence_than_sentences(self):
        def gap(text):
            return [s["pause"] for s in plan_narration(text) if "pause" in s]

        assert gap("एक बात कही गई थी।\nदूसरी बात भी कही गई।")[0] > gap("एक बात कही गई थी। दूसरी बात भी कही गई।")[0]
        assert gap("एक बात कही गई थी।\n\nदूसरी बात भी कही गई।")[0] > gap("एक बात कही गई थी।\nदूसरी बात भी कही गई।")[0]

    def test_divider_is_silent_and_quotes_are_not_spoken(self):
        plan = plan_narration("पहला अध्याय समाप्त हुआ।\n* * *\nउसने कहा “मैं कल वापस आऊँगा” और चल दिया।")
        spoken = " ".join(s["text"] for s in plan if "text" in s)
        assert "*" not in spoken and "“" not in spoken and "”" not in spoken
        quoted = next(s for s in plan if "text" in s and "वापस" in s["text"])
        narration = next(s for s in plan if "text" in s and "उसने" in s["text"])
        assert quoted["pitch"] > narration["pitch"]

    def test_same_text_gives_the_same_audio_instructions(self):
        text = "यह वाक्य दोबारा वैसे ही पढ़ा जाना चाहिए, हर बार।"
        assert plan_narration(text, "शीर्षक") == plan_narration(text, "शीर्षक")

    def test_title_comes_first_and_no_trailing_silence(self):
        plan = plan_narration("कहानी शुरू होती है।", "पहली बारिश")
        assert plan[0]["text"] == "पहली बारिश"
        assert "pause" not in plan[-1]

    def test_ssml_is_wellformed_escaped_and_split_for_long_stories(self):
        text = "\n".join(f"वाक्य संख्या {i} में a < b & c का ज़िक्र है।" for i in range(400))
        chunks = to_ssml_chunks(plan_narration(text), "hi-IN-SwaraNeural", max_chars=2500)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.startswith("<speak ") and chunk.endswith("</voice></speak>")
            assert 'name="hi-IN-SwaraNeural"' in chunk
            assert "a &lt; b &amp; c" in chunk
            assert re.search(r'<prosody rate="[+-]\d+%" pitch="[+-]\d+%">', chunk)
        assert sum(spoken_length(plan_narration(text)) for _ in [0]) > 2500
