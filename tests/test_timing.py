"""Word timings for the follow-along highlight, checked against audio whose true timing is known."""

import math
import struct

from app.core import timing
from app.core.narration import Turn, Window, scan

RATE = timing.SAMPLE_RATE


def pcm(parts):
    """16-bit mono audio from [(seconds, loud?)]: a steady tone, or silence."""
    out = bytearray()
    for seconds, loud in parts:
        count = int(RATE * seconds)
        if loud:
            out += b"".join(struct.pack("<h", int(7000 * math.sin(2 * math.pi * 220 * n / RATE))) for n in range(count))
        else:
            out += b"\x00\x00" * count
    return bytes(out)


WORD = "अबकद"  # four letters: every word weighs the same, so only the audio can tell speeds apart
FAST, SLOW, GAP = 0.15, 0.45, 0.5
TEXT = f"{' '.join([WORD] * 4)} {WORD}। {' '.join([WORD] * 4)} {WORD}।"  # two sentences of five words
UNITS = timing.units_from_text(TEXT)
# True start of each word: five fast words, a pause, five slow words.
TRUTH = [i * FAST for i in range(5)] + [5 * FAST + GAP + i * SLOW for i in range(5)]
AUDIO = pcm([(0.3, False), (5 * FAST, True), (GAP, False), (5 * SLOW, True), (0.4, False)])  # lead + trail silence


def worst_error(times, offset=0.3):
    return max(abs(t - (truth + offset)) for t, truth in zip(times, TRUTH))


def test_the_speech_is_found_inside_its_silences_and_the_pause_between_sentences_is_a_gap():
    start, end, gaps = timing.analyze_pcm(AUDIO)
    assert abs(start - 0.3) < 0.03
    assert abs(end - (0.3 + 5 * FAST + GAP + 5 * SLOW)) < 0.03
    assert len(gaps) == 1 and abs(gaps[0][0] - (0.3 + 5 * FAST)) < 0.03 and abs(gaps[0][1] - gaps[0][0] - GAP) < 0.05


def test_words_are_fitted_to_the_real_pause_not_just_to_their_length():
    start, end, gaps = timing.analyze_pcm(AUDIO)
    guess = timing.word_times(UNITS, start, end)  # length only
    fitted = timing.word_times(UNITS, start, end, gaps)
    assert worst_error(guess) > 0.3  # a plain spread is badly off when speeds differ
    assert worst_error(fitted) < 0.06  # the matched pause puts every word within a frame or two
    assert fitted == sorted(fitted)


def test_an_unexpected_breath_inside_a_sentence_does_not_throw_the_words_off():
    breath = pcm([(0.3, False), (5 * FAST, True), (GAP, False), (2 * SLOW, True), (0.2, False), (3 * SLOW, True), (0.4, False)])
    start, end, gaps = timing.analyze_pcm(breath)
    assert len(gaps) == 2
    fitted = timing.word_times(UNITS, start, end, gaps)
    assert fitted == sorted(fitted)
    assert max(abs(t - (truth + 0.3)) for t, truth in list(zip(fitted, TRUTH))[:5]) < 0.06  # sentence 1 is exact
    assert max(abs(t - (truth + 0.3)) for t, truth in list(zip(fitted, TRUTH))[5:]) < 0.4  # sentence 2 is close


def test_a_missing_pause_falls_back_to_the_length_guess_and_never_fails():
    no_gap = pcm([(0.3, False), (3.0, True), (0.3, False)])
    start, end, gaps = timing.analyze_pcm(no_gap)
    assert gaps == []
    times = timing.word_times(UNITS, start, end, gaps)
    assert len(times) == 10 and times == sorted(times) and times[0] >= start and times[-1] <= end


def test_silence_only_or_empty_audio_is_handled():
    assert timing.analyze_pcm(b"")[2] == []
    start, end, gaps = timing.analyze_pcm(b"\x00\x00" * RATE)
    assert gaps == [] and end >= start
    assert timing.word_times([], 0, 1) == []
    assert timing.word_times([("एक", "")], 0.0, 1.0) == [0.0]


def test_words_and_their_pauses_are_read_from_the_text():
    units = timing.units_from_text("पहला, दूसरा शब्द। तीसरा\n\nचौथा “शब्द”")
    assert [(w, k) for w, k in units] == [
        ("पहला,", "clause"), ("दूसरा", ""), ("शब्द।", "sentence"), ("तीसरा", "paragraph"),
        ("चौथा", ""), ("“शब्द”", ""),
    ]
    window = Window([Turn("a", False, "एक दो।", True), Turn("b", True, "तीन", False)])
    assert [k for _, k in timing.units_for_window(window)] == ["", "turn", ""]


def test_the_whole_story_gets_an_estimate_from_its_length_alone():
    paragraphs = scan("पहली पंक्ति यहाँ है।\nदूसरी पंक्ति भी यहाँ है।")
    result = timing.estimate_timings("शीर्षक यहाँ", paragraphs, 20.0)
    assert result["title"] == 2 and result["d"] == 20.0
    assert result["w"][:3] == ["शीर्षक", "यहाँ", "पहली"]
    assert result["t"] == sorted(result["t"])
    assert abs(result["t"][0] - (timing.ESTIMATE_LEAD + timing.MP3_DELAY)) < 0.01  # after the opening silence
    assert result["t"][-1] < 20.0 - timing.ESTIMATE_TRAIL
    assert timing.estimate_timings("", [], 20.0) is None and timing.estimate_timings("x", paragraphs, 0) is None


def test_a_story_in_two_requests_has_continuous_timings_that_include_the_silence_between_them():
    from app.core import speech

    first = Window([Turn("v", False, f"{' '.join([WORD] * 4)} {WORD}।", True)])
    second = Window([Turn("v", False, f"{' '.join([WORD] * 4)} {WORD}।", True)], pause=0.7)
    audio_one = pcm([(0.2, False), (5 * FAST, True), (0.2, False)])
    audio_two = pcm([(0.1, False), (5 * SLOW, True), (0.3, False)])
    out = {}
    mp3 = speech.synthesize([first, second], "n", "d", cache={0: audio_one, 1: audio_two}, title_words=2, timings_out=out)
    assert mp3
    assert out["title"] == 2 and len(out["w"]) == 10 and out["t"] == sorted(out["t"])
    one = 0.2 + 5 * FAST + 0.2  # seconds of the first request's audio
    second_starts = one + 0.7 + 0.1  # then the inserted silence, then that request's own lead-in
    delay = timing.MP3_DELAY  # the player starts the sound a little late; the times say when it is heard
    assert abs(out["t"][0] - (0.2 + delay)) < 0.04
    assert abs(out["t"][5] - (second_starts + delay)) < 0.06
    assert abs(out["d"] - (one + 0.7 + 0.1 + 5 * SLOW + 0.3 + delay)) < 0.01
