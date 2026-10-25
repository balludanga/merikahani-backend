"""Server-side narration: access rules, background job, casting and voices, Gemini requests."""

import base64
import io
import threading
import time
import wave
from unittest.mock import MagicMock, patch

import pytest

from app.api.endpoints import audio as audio_endpoint
from app.core import casting, narration, speech
from app.core.config import settings
from app.core.narration import Turn, Window, build_turns, build_windows, scan, tagged_text
from app.core.speech import RateLimited, SpeechUnavailable
from app.database import SessionLocal
from app.models.story_audio import StoryAudio

SHORT = "एक छोटी सी कहानी, जो बहुत जल्दी खत्म हो जाती है।"
LONG = "यह एक लंबी रचना का नमूना अंश है। " * 80  # past the preview limit

PLAIN_CAST = {"narrator_gender": "unknown", "speakers": [], "quotes": []}


@pytest.fixture(autouse=True)
def _clean():
    """Every test starts with no key (whatever the developer's shell has), no jobs, no quota blocks."""
    audio_endpoint._jobs.clear()
    audio_endpoint._partials.clear()
    speech.reset_block()
    with patch.object(settings, "GEMINI_API_KEY", ""):
        yield
    audio_endpoint._jobs.clear()
    audio_endpoint._partials.clear()
    speech.reset_block()


@pytest.fixture
def gemini_on():
    """Pretend a key is set; casting and speech are replaced by recorders. Yields the windows read."""
    calls = []

    def fake(windows, style, dialogue_style, progress=None, cache=None):
        calls.append(windows)
        if progress:
            for i in range(1, len(windows) + 1):
                progress(i, len(windows))
        return b"ID3-fake-mp3-" + str(len(calls)).encode()

    with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch.object(
        casting, "generate_json", return_value=PLAIN_CAST
    ), patch.object(audio_endpoint, "synthesize", side_effect=fake):
        yield calls


def _post(client, writer, content, title="सुनने की रचना", published=1):
    response = client.post(
        "/api/posts", json={"title": title, "content": content, "published": published}, headers=writer["headers"]
    )
    assert response.status_code == 201, response.text
    return response.json()


def _listen(client, post_id, headers=None):
    """Ask for the audio the way the page does: again and again until it is no longer 202."""
    for _ in range(100):
        response = client.get(f"/api/audio/post/{post_id}", headers=headers or {})
        if response.status_code != 202:
            return response
        time.sleep(0.05)
    raise AssertionError("the narration never finished")


# --- the endpoint ---------------------------------------------------------------------------


def test_status_tells_the_page_whether_to_use_the_server_voice(test_client):
    assert test_client.get("/api/audio/status").json() == {"enabled": False}
    with patch.object(settings, "GEMINI_API_KEY", "test-key"):
        assert test_client.get("/api/audio/status").json() == {"enabled": True}
        for model in speech.tts_models():  # every speech model out of allowance for today
            speech._block_model(model, 3600)
        assert test_client.get("/api/audio/status").json() == {"enabled": False}


def test_off_without_a_key(test_client, writer_a):
    post = _post(test_client, writer_a, SHORT)
    assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 503


def test_short_story_is_open_to_everyone_and_cached(test_client, writer_a, gemini_on):
    post = _post(test_client, writer_a, SHORT)
    first = _listen(test_client, post["id"])
    assert first.status_code == 200
    assert first.headers["content-type"] == "audio/mpeg"
    assert first.headers["cache-control"].startswith("private")
    assert first.content.startswith(b"ID3")

    second = test_client.get(f"/api/audio/post/{post['id']}")
    assert second.status_code == 200
    assert second.content == first.content
    assert len(gemini_on) == 1  # the second listen cost nothing

    cached = test_client.get(f"/api/audio/post/{post['id']}", headers={"If-None-Match": first.headers["etag"]})
    assert cached.status_code == 304


def test_the_title_is_read_first(test_client, writer_a, gemini_on):
    post = _post(test_client, writer_a, SHORT, title="पहली बारिश")
    assert _listen(test_client, post["id"]).status_code == 200
    assert gemini_on[0][0].turns[0].text.startswith("पहली बारिश")


def test_long_story_needs_sign_in_like_reading_does(test_client, writer_a, writer_b, gemini_on):
    post = _post(test_client, writer_a, LONG)
    assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 401
    assert len(gemini_on) == 0
    assert _listen(test_client, post["id"], headers=writer_b["headers"]).status_code == 200


def test_drafts_are_never_sent_to_the_speech_service(test_client, writer_a, gemini_on):
    post = _post(test_client, writer_a, SHORT, published=0)
    assert test_client.get(f"/api/audio/post/{post['id']}", headers=writer_a["headers"]).status_code == 404
    assert len(gemini_on) == 0


def test_editing_the_story_makes_new_audio(test_client, writer_a, gemini_on):
    post = _post(test_client, writer_a, SHORT)
    assert _listen(test_client, post["id"]).status_code == 200
    updated = test_client.put(
        f"/api/posts/{post['id']}", json={"content": SHORT + " और एक वाक्य जुड़ गया।"}, headers=writer_a["headers"]
    )
    assert updated.status_code == 200, updated.text
    assert _listen(test_client, post["id"]).status_code == 200
    assert len(gemini_on) == 2


def test_too_long_a_story_is_left_to_the_browser_voice(test_client, writer_a, gemini_on):
    post = _post(test_client, writer_a, "शब्द। " * 3000)
    with patch.object(settings, "TTS_MAX_STORY_CHARS", 1000):
        assert test_client.get(f"/api/audio/post/{post['id']}", headers=writer_a["headers"]).status_code == 413
    assert len(gemini_on) == 0


def test_monthly_budget_stops_new_audio_but_not_cached_audio(test_client, writer_a, gemini_on):
    heard = _post(test_client, writer_a, SHORT, title="पहले से बनी")
    assert _listen(test_client, heard["id"]).status_code == 200
    fresh = _post(test_client, writer_a, SHORT + " नई रचना।", title="बजट के बाद")
    with patch.object(settings, "TTS_MONTHLY_CHARS", 1):
        assert test_client.get(f"/api/audio/post/{fresh['id']}").status_code == 503
        assert test_client.get(f"/api/audio/post/{heard['id']}").status_code == 200


def test_progress_is_reported_while_the_story_is_being_read(test_client, writer_a):
    post = _post(test_client, writer_a, SHORT, title="धीमी रचना")
    started, release = threading.Event(), threading.Event()

    def slow(windows, style, dialogue_style, progress=None, cache=None):
        progress(1, 3)
        started.set()
        release.wait(5)
        return b"ID3-slow"

    with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch.object(
        casting, "generate_json", return_value=PLAIN_CAST
    ), patch.object(audio_endpoint, "synthesize", side_effect=slow):
        assert test_client.get(f"/api/audio/post/{post['id']}").status_code == 202
        assert started.wait(5)
        waiting = test_client.get(f"/api/audio/post/{post['id']}")
        assert waiting.status_code == 202
        assert waiting.json()["state"] == "working" and waiting.json()["done"] == 1
        release.set()
        assert _listen(test_client, post["id"]).status_code == 200


def test_trouble_with_the_service_becomes_a_fallback_status_and_nothing_is_stored(test_client, writer_a):
    post = _post(test_client, writer_a, SHORT + " अलग पाठ।", title="विफल")
    with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch.object(
        casting, "generate_json", return_value=PLAIN_CAST
    ), patch.object(audio_endpoint, "synthesize", side_effect=SpeechUnavailable("status 500")):
        assert _listen(test_client, post["id"]).status_code == 503
    with SessionLocal() as db:
        assert db.query(StoryAudio).filter(StoryAudio.post_id == post["id"]).count() == 0
    # once the failure has been forgotten, the next listen tries again
    audio_endpoint._partials.clear()
    with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch.object(
        casting, "generate_json", return_value=PLAIN_CAST
    ), patch.object(audio_endpoint, "synthesize", return_value=b"ID3-ok"), patch.object(
        audio_endpoint, "FAILURE_MEMORY", 0
    ):
        assert _listen(test_client, post["id"]).status_code == 200


def test_a_story_cut_short_by_the_daily_allowance_resumes_without_paying_twice(test_client, writer_a):
    post = _post(test_client, writer_a, SHORT + " अधूरी रचना।", title="अधूरी")
    seen = {"plans": 0, "cache_at_retry": None}

    def plan(post_id, title, content):
        seen["plans"] += 1
        return [Window([Turn("v1", False, "पहला", True)]), Window([Turn("v1", False, "दूसरा", True)], 0.7)], None

    def flaky(windows, style, dialogue_style, progress=None, cache=None):
        if not seen["cache_at_retry"] and 0 not in cache:
            cache[0] = b"first-window-audio"  # window 1 got read ...
            raise RateLimited(3600)  # ... then the allowance ran out
        seen["cache_at_retry"] = dict(cache)
        return b"ID3-whole"

    with patch.object(settings, "GEMINI_API_KEY", "test-key"), patch.object(
        audio_endpoint, "plan_story", side_effect=plan
    ), patch.object(audio_endpoint, "synthesize", side_effect=flaky):
        assert _listen(test_client, post["id"]).status_code == 503
        with patch.object(audio_endpoint, "FAILURE_MEMORY", 0):  # the failure is forgotten: try again
            assert _listen(test_client, post["id"]).status_code == 200
    assert seen["plans"] == 1  # the same plan was reused
    assert seen["cache_at_retry"] == {0: b"first-window-audio"}  # and window 1 was not read again


def test_deleting_a_story_removes_its_audio(test_client, writer_a, gemini_on):
    post = _post(test_client, writer_a, SHORT + " मिटने वाली।", title="मिटेगी")
    assert _listen(test_client, post["id"]).status_code == 200
    assert test_client.delete(f"/api/posts/{post['id']}", headers=writer_a["headers"]).status_code in (200, 204)
    with SessionLocal() as db:
        assert db.query(StoryAudio).filter(StoryAudio.post_id == post["id"]).count() == 0


# --- reading the story: quotes, turns, windows ----------------------------------------------

DIALOGUE_STORY = """मैं रसोई में चाय बना रही थी जब राजेश आया।
“इतनी देर क्यों हो गई?” मैंने पूछा।
“दफ़्तर में काम था,” उसने कहा। “माँ कहाँ हैं?”
* * *
अंदर से माँ बोलीं, “मैं यहीं हूँ बेटा।”"""


class TestScan:
    def test_quotes_are_numbered_in_reading_order_and_dividers_dropped(self):
        paragraphs = scan(DIALOGUE_STORY)
        assert len(paragraphs) == 4  # the "* * *" line is not speech
        quotes = [(s.q, s.text) for spans in paragraphs for s in spans if s.kind == "quote"]
        assert quotes == [
            (1, "इतनी देर क्यों हो गई?"), (2, "दफ़्तर में काम था,"), (3, "माँ कहाँ हैं?"), (4, "मैं यहीं हूँ बेटा।"),
        ]

    def test_straight_quotes_work_and_an_unclosed_quote_mark_is_not_spoken(self):
        paragraphs = scan('उसने कहा "ठीक है" और चला गया।\nखुला “उद्धरण यहाँ रह गया।')
        assert [s.text for s in paragraphs[0] if s.kind == "quote"] == ["ठीक है"]
        assert "“" not in " ".join(s.text for p in paragraphs for s in p)

    def test_the_story_is_labelled_for_the_cast_reader_without_changing_it(self):
        tagged = tagged_text("चाय", scan(DIALOGUE_STORY))
        assert tagged.startswith("शीर्षक: चाय")
        assert "[Q1]“इतनी देर क्यों हो गई?”" in tagged and "[Q4]“मैं यहीं हूँ बेटा।”" in tagged
        assert "मैं रसोई में चाय बना रही थी" in tagged


class TestWindows:
    def _turns(self, voices_by_quote):
        return build_turns("शीर्षक", scan(DIALOGUE_STORY), "narr", lambda q: voices_by_quote[q])

    def test_a_conversation_between_two_voices_is_one_request(self):
        windows = build_windows(self._turns({1: "narr", 2: "raj", 3: "raj", 4: "narr"}))
        assert len(windows) == 1
        assert windows[0].voices == ["narr", "raj"]

    def test_never_more_than_two_voices_in_a_request(self):
        windows = build_windows(self._turns({1: "narr", 2: "raj", 3: "raj", 4: "maa"}))
        assert len(windows) == 2
        assert all(len(w.voices) <= 2 for w in windows)
        assert [t.voice for w in windows for t in w.turns][-1] == "maa"
        assert windows[1].pause > 0 and windows[0].pause == 0

    def test_a_request_holds_at_most_the_configured_characters(self):
        turns = [Turn("narr", False, "वाक्य। " * 20, True) for _ in range(10)]
        with patch.object(settings, "TTS_WINDOW_CHARS", 300):
            windows = build_windows(turns)
        assert len(windows) > 1
        assert all(w.chars <= 300 for w in windows)

    def test_a_new_paragraph_is_a_longer_silence_than_the_next_line_of_a_conversation(self):
        turns = [Turn("a", False, "x" * 50, True), Turn("b", True, "y", False), Turn("c", False, "z", True)]
        windows = build_windows(turns, limit=10_000)
        assert [w.voices for w in windows] == [["a", "b"], ["c"]]
        assert windows[1].pause == narration.PARAGRAPH_PAUSE

    def test_neighbouring_pieces_by_one_voice_are_joined_and_a_monologue_is_cut_at_sentence_ends(self):
        turns = build_turns("", scan("पहली बात।\nदूसरी बात।"), "narr", lambda q: "narr")
        assert len(turns) == 1 and turns[0].text == "पहली बात।\n\nदूसरी बात।"
        monologue = " ".join("यह एक पूरा वाक्य है जो अपनी जगह खत्म होता है।" for _ in range(60))
        pieces = build_turns("", scan(monologue), "narr", lambda q: "narr", limit=400)
        assert len(pieces) > 1 and all(len(t.text) <= 400 and t.text.endswith("।") for t in pieces)


# --- who tells the story, who speaks --------------------------------------------------------


class TestCasting:
    def test_garbage_from_the_model_cannot_break_anything(self):
        junk_answers = (
            None, [], "x",
            {"narrator_gender": 5, "speakers": "no", "quotes": {"q": 1}},
            {"speakers": [{"id": "S1"}, 7, {"id": "narrator", "name": "x", "gender": "male"}],
             "quotes": [{"q": "a"}, {"q": 99, "speaker": "S1"}]},
        )
        for junk in junk_answers:
            cast = casting._clean(junk, quote_count=4)
            assert cast.narrator_gender == "unknown"
            assert cast.quote_speaker == {} and "narrator" not in cast.speakers

    def test_labels_are_kept_when_well_formed(self):
        cast = casting._clean(
            {"narrator_gender": "female",
             "speakers": [{"id": "S1", "name": "राजेश", "gender": "male"}],
             "quotes": [{"q": 1, "speaker": "narrator"}, {"q": 2, "speaker": "S1"}, {"q": 3, "speaker": "S9"}]},
            quote_count=3,
        )
        assert cast.narrator_gender == "female"
        assert cast.speakers["S1"].gender == "male"
        assert cast.quote_speaker == {1: "narrator", 2: "S1"}  # an unknown speaker id is dropped

    def _pools(self):
        return patch.multiple(settings, TTS_FEMALE_VOICES="f1,f2,f3", TTS_MALE_VOICES="m1,m2,m3")

    def test_a_womans_story_gets_a_womans_voice_and_a_mans_story_a_mans(self):
        with self._pools():
            for seed in range(20):
                voice, _ = casting.assign_voices(casting.Cast("female"), seed)
                assert voice.startswith("f")
                voice, _ = casting.assign_voices(casting.Cast("male"), seed)
                assert voice.startswith("m")

    def test_each_character_gets_a_voice_of_their_gender_and_all_voices_differ(self):
        cast = casting.Cast(
            "female",
            {
                "S1": casting.Speaker("S1", "राजेश", "male"),
                "S2": casting.Speaker("S2", "माँ", "female"),
                "S3": casting.Speaker("S3", "पापा", "male"),
            },
            {1: "narrator", 2: "S1", 3: "S2", 4: "S3"},
        )
        with self._pools():
            for seed in range(20):
                narrator, voice_for = casting.assign_voices(cast, seed)
                voices = [voice_for(q) for q in (1, 2, 3, 4)]
                assert voices[0] == narrator and narrator.startswith("f")
                assert voices[1].startswith("m") and voices[2].startswith("f") and voices[3].startswith("m")
                assert len(set(voices)) == 4  # three women's / men's voices are enough to keep all four apart

    def test_the_draw_is_the_same_every_time_for_a_story_but_varies_between_stories(self):
        cast = casting.Cast("female")
        with self._pools():
            assert casting.assign_voices(cast, 5)[0] == casting.assign_voices(cast, 5)[0]
            assert len({casting.assign_voices(cast, casting.seed_for(i))[0] for i in range(1, 40)}) > 1

    def test_unclear_narrator_in_a_story_of_women_speaking_is_a_woman(self):
        cast = casting.Cast(
            "unknown", {"S1": casting.Speaker("S1", "रेखा", "female"), "S2": casting.Speaker("S2", "सीमा", "female")}, {}
        )
        with self._pools():
            assert all(casting.assign_voices(cast, seed)[0].startswith("f") for seed in range(20))

    def test_unclear_narrator_with_a_mixed_cast_is_left_to_chance(self):
        cast = casting.Cast(
            "unknown", {"S1": casting.Speaker("S1", "रेखा", "female"), "S2": casting.Speaker("S2", "राम", "male")}, {}
        )
        with self._pools():
            assert {casting.assign_voices(cast, seed)[0][0] for seed in range(40)} == {"f", "m"}

    def test_a_pool_left_empty_by_mistake_borrows_the_other_pool(self):
        with patch.multiple(settings, TTS_FEMALE_VOICES="", TTS_MALE_VOICES="m1,m2"):
            assert casting.assign_voices(casting.Cast("female"), 1)[0] in ("m1", "m2")

    def test_the_whole_story_is_planned_with_the_voices_the_labels_say(self):
        answer = {
            "narrator_gender": "female",
            "speakers": [
                {"id": "S1", "name": "राजेश", "gender": "male"},
                {"id": "S2", "name": "माँ", "gender": "female"},
            ],
            "quotes": [
                {"q": 1, "speaker": "narrator"}, {"q": 2, "speaker": "S1"},
                {"q": 3, "speaker": "S1"}, {"q": 4, "speaker": "S2"},
            ],
        }
        with patch.object(casting, "generate_json", return_value=answer), patch.multiple(
            settings, TTS_FEMALE_VOICES="f1,f2,f3", TTS_MALE_VOICES="m1,m2,m3"
        ):
            windows, cast = casting.plan_story(7, "चाय", DIALOGUE_STORY)
        by_text = {t.text: t.voice for w in windows for t in w.turns if t.dialogue}
        assert by_text["दफ़्तर में काम था,"].startswith("m") and by_text["मैं यहीं हूँ बेटा।"].startswith("f")
        assert by_text["इतनी देर क्यों हो गई?"].startswith("f")  # the narrator herself asking
        assert all(len(w.voices) <= 2 for w in windows)

    def test_if_the_story_cannot_be_read_it_is_still_narrated_in_one_voice(self):
        with patch.object(casting, "generate_json", side_effect=SpeechUnavailable("busy")):
            windows, cast = casting.plan_story(7, "चाय", DIALOGUE_STORY)
        assert cast.narrator_gender == "unknown"
        assert len({v for w in windows for v in w.voices}) == 1
        assert len(windows) >= 1


# --- the Gemini client ----------------------------------------------------------------------


def _wav(seconds=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000 * seconds)
    return buffer.getvalue()


def _answer(wav):
    return {"steps": [{"content": [{"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}]}]}


def _reply(status=200, body=None, headers=None):
    reply = MagicMock()
    reply.status_code = status
    reply.json.return_value = body if body is not None else {}
    reply.text = "x"
    reply.headers = headers or {}
    return reply


ONE = Window([Turn("hi-in-storyteller-6", False, "नमस्ते।", True)])
TWO = Window([
    Turn("hi-in-storyteller-6", False, "उसने पूछा।", True),
    Turn("hi-in-podcaster-8", True, "कहाँ जा रहे हो?", False),
    Turn("hi-in-storyteller-6", False, "वह हँसा।", False),
])


class TestGeminiClient:
    def test_one_voice_is_a_plain_request_with_the_voice_and_style(self):
        body = speech._window_body("m", ONE, "narr-style", "talk-style")
        assert body["model"] == "m"
        assert body["response_format"] == {"type": "audio"}
        assert body["generation_config"]["speech_config"] == [{"voice": "hi-in-storyteller-6"}]
        (part,) = body["input"][0]["content"]
        assert part["text"] == "नमस्ते।"
        assert part["annotations"] == [{"type": "speech_metadata", "style": "narr-style"}]

    def test_two_voices_are_one_conversation_with_a_speaker_on_every_turn(self):
        body = speech._window_body("m", TWO, "narr-style", "talk-style")
        config = body["generation_config"]["speech_config"]
        assert config == {"speakers": [
            {"speaker": "Narrator", "voice": "hi-in-storyteller-6"},
            {"speaker": "Character2", "voice": "hi-in-podcaster-8"},
        ]}
        content = body["input"][0]["content"]
        assert [(c["annotations"][0]["speaker"], c["annotations"][0]["style"]) for c in content] == [
            ("Narrator", "narr-style"), ("Character2", "talk-style"), ("Narrator", "narr-style")]
        assert [c["text"] for c in content] == ["उसने पूछा।", "कहाँ जा रहे हो?", "वह हँसा।"]

    def test_audio_is_found_even_if_nested_differently(self):
        data = base64.b64encode(_wav()).decode()
        assert speech._find_audio({"a": [{"b": {"mime_type": "audio/wav", "data": data}}]}) == ("audio/wav", data)
        assert speech._find_audio({"text": "hello", "data": "short"}) is None

    def test_headerless_l16_is_accepted(self):
        assert speech._pcm_from("audio/l16", b"\x00\x01" * 10) == b"\x00\x01" * 10

    def test_the_first_model_with_allowance_reads_the_window(self):
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch(
            "app.core.speech.requests.post", return_value=_reply(body=_answer(_wav()))
        ) as post:
            pcm, model = speech.synthesize_window(ONE, "n", "d")
        assert len(pcm) == 48000
        assert model == speech.tts_models()[0]
        assert post.call_args[1]["json"]["model"] == model
        assert post.call_args[1]["headers"]["x-goog-api-key"] == "k"

    def test_a_model_whose_daily_allowance_is_gone_is_skipped_for_the_next(self):
        replies = [_reply(status=429, headers={"Retry-After": "40000"}), _reply(body=_answer(_wav()))]
        first, second = speech.tts_models()[:2]
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch(
            "app.core.speech.requests.post", side_effect=replies
        ) as post:
            _, model = speech.synthesize_window(ONE, "n", "d")
            assert model == second
            assert speech.is_available()  # the others still have theirs
            post.side_effect = [_reply(body=_answer(_wav()))]
            speech.synthesize_window(ONE, "n", "d")  # the first is not even asked again
        assert [c[1]["json"]["model"] for c in post.call_args_list] == [first, second, second]

    def test_when_every_model_is_used_up_the_server_voice_pauses(self):
        replies = [_reply(status=429, headers={"Retry-After": "40000"}) for _ in speech.tts_models()]
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch("app.core.speech.requests.post", side_effect=replies):
            with pytest.raises(RateLimited):
                speech.synthesize_window(ONE, "n", "d")
            assert speech.is_available() is False
        speech.reset_block()
        with patch.object(settings, "GEMINI_API_KEY", "k"):
            assert speech.is_available() is True

    def test_a_model_that_refuses_the_request_hands_over_to_the_next(self):
        replies = [_reply(status=400), _reply(body=_answer(_wav()))]
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch("app.core.speech.requests.post", side_effect=replies):
            _, model = speech.synthesize_window(ONE, "n", "d")
        assert model == speech.tts_models()[1]

    def test_a_brief_rate_limit_is_waited_out(self):
        replies = [_reply(status=429, headers={"Retry-After": "0"}), _reply(body=_answer(_wav()))]
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch("app.core.speech.requests.post", side_effect=replies), patch(
            "app.core.speech.time.sleep"
        ):
            pcm, _ = speech.synthesize_window(ONE, "n", "d")
        assert len(pcm) == 48000

    def test_an_answer_with_no_audio_is_unavailable(self):
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch(
            "app.core.speech.requests.post", return_value=_reply(body={"steps": []})
        ):
            with pytest.raises(SpeechUnavailable):
                speech.synthesize_window(ONE, "n", "d")

    def test_the_story_is_joined_with_silences_compressed_to_mp3_and_finished_windows_are_kept(self):
        windows = [Window([Turn("v", False, "एक।", True)]), Window([Turn("v", False, "दो।", True)], pause=0.5)]
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch(
            "app.core.speech.requests.post", return_value=_reply(body=_answer(_wav(2)))
        ) as post:
            seen, cache = [], {}
            mp3 = speech.synthesize(windows, "n", "d", lambda done, total: seen.append((done, total)), cache)
            assert seen == [(1, 2), (2, 2)] and set(cache) == {0, 1}
            assert mp3[:3] == b"ID3" or mp3[0] == 0xFF  # an MP3 stream
            assert len(mp3) < 5 * 48000 // 4  # ~4.5 s of audio at 48 kbps, far below the raw 216 KB
            before = post.call_count
            speech.synthesize(windows, "n", "d", None, cache)
            assert post.call_count == before  # nothing was read again

    def test_the_text_model_is_asked_in_turn_and_a_busy_one_does_not_pause_the_voice(self):
        good = {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}
        replies = [_reply(status=503), _reply(body=good)]
        with patch.object(settings, "GEMINI_API_KEY", "k"), patch(
            "app.core.speech.requests.post", side_effect=replies
        ) as post:
            assert speech.generate_json("p", {"type": "OBJECT"}) == {"ok": 1}
            assert speech.is_available()
        models = settings.CAST_MODELS.split(",")
        assert [c[0][0].split("models/")[1].split(":")[0] for c in post.call_args_list] == models[:2]
        assert post.call_args_list[0][1]["json"]["generationConfig"]["responseMimeType"] == "application/json"
