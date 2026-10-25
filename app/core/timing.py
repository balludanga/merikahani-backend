"""When is each word spoken? Timings so the page can highlight the word being narrated.

The speech model returns audio but no word times, so they are worked out here:

1. A first guess spreads the audio over the words by their length, with room for the pauses
   that punctuation and paragraph ends bring (a comma is a short breath, a full stop a longer
   one, a new paragraph or speaker longer still).
2. The real pauses in the audio are then found (short stretches of near-silence). The model
   pauses where the punctuation is, so each expected pause is matched to a real one, in order,
   and the words between two matched pauses are spread over exactly the time between them.
   Without this a guess made over three minutes would drift by several words; with it the error
   stays inside one clause.

Old narrations made before timings were kept get step 1 only, over the length of the MP3.
"""

import json
import re
from array import array
from typing import List, Optional, Sequence, Tuple

SAMPLE_RATE = 24000
FRAME = 480  # 20 ms of audio
FRAME_SECONDS = FRAME / SAMPLE_RATE
MIN_GAP = 0.15  # shortest stretch of near-silence taken for a pause between words

# Pause room, in "letters" of speaking time, that punctuation leaves after a word. Measured on a
# real narration (about 11 letters a second): a full stop or a line break is roughly a second of
# silence, a comma between a quarter and half a second.
PAUSE_WEIGHT = {"": 0.0, "clause": 4.5, "sentence": 11.0, "turn": 7.0, "paragraph": 11.0}

# The MP3 player starts the sound this much later than the audio we cut it from (the encoder's
# delay: measured 46 ms in Chrome), so every word is spoken that much later than it was made.
MP3_DELAY = 0.046
# A Gemini narration begins and ends with a little silence (typically 0.3 s and 0.6 s).
ESTIMATE_LEAD = 0.3
ESTIMATE_TRAIL = 0.6
_SENTENCE_END = re.compile(r"[।॥?!][”\"’)]*$|\.[”\"’)]*$")
_CLAUSE_END = re.compile(r"[,;:—–…][”\"’)]*$")
_LETTERS = re.compile(r"[ऀ-ॣ०-ॿ\w]")

# (word, kind of pause after it)
Unit = Tuple[str, str]


def _pause_after(token: str) -> str:
    if _SENTENCE_END.search(token):
        return "sentence"
    if _CLAUSE_END.search(token):
        return "clause"
    return ""


def units_from_text(text: str, last_kind: str = "") -> List[Unit]:
    """Words of ``text`` ("\\n\\n" separates paragraphs), each with the pause that follows it."""
    units: List[Unit] = []
    paragraphs = text.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        words = paragraph.split()
        for position, word in enumerate(words):
            if position < len(words) - 1:
                units.append((word, _pause_after(word)))
            elif index < len(paragraphs) - 1:
                units.append((word, "paragraph"))
            else:
                units.append((word, last_kind))
    return units


def units_for_window(window) -> List[Unit]:
    """The words one speech request reads, in order; the last word of each turn is followed by a turn pause."""
    units: List[Unit] = []
    last = len(window.turns) - 1
    for index, turn in enumerate(window.turns):
        units.extend(units_from_text(turn.text, "turn" if index < last else ""))
    return units


def units_for_story(title: str, paragraphs) -> List[Unit]:
    """The whole story as words with pause kinds, for an estimate that needs no audio analysis."""
    units = units_from_text(title.strip(), "paragraph") if title.strip() else []
    for spans in paragraphs:
        text = " ".join(span.text for span in spans)
        units.extend(units_from_text(text, "paragraph"))
    return units


def _weight(word: str) -> float:
    return float(max(1, len(_LETTERS.findall(word))))


def analyze_pcm(pcm: bytes) -> Tuple[float, float, List[Tuple[float, float]]]:
    """(speech start, speech end, pauses inside the speech), all in seconds, from 16-bit mono audio."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if samples and array("H", [1]).tobytes() != b"\x01\x00":  # big-endian host
        samples.byteswap()
    frames = len(samples) // FRAME
    if frames == 0:
        return 0.0, len(samples) / SAMPLE_RATE, []
    energy = [sum(map(abs, samples[i * FRAME:(i + 1) * FRAME])) / FRAME for i in range(frames)]
    loud = sorted(energy)[int(0.9 * (frames - 1))]
    threshold = max(60.0, loud * 0.05)
    quiet = [e < threshold for e in energy]

    first = next((i for i, q in enumerate(quiet) if not q), None)
    if first is None:
        return 0.0, frames * FRAME_SECONDS, []
    last = frames - 1 - next(i for i, q in enumerate(reversed(quiet)) if not q)
    start, end = first * FRAME_SECONDS, (last + 1) * FRAME_SECONDS

    gaps: List[Tuple[float, float]] = []
    i = first
    while i <= last:
        if quiet[i]:
            j = i
            while j <= last and quiet[j]:
                j += 1
            if (j - i) * FRAME_SECONDS >= MIN_GAP:
                gaps.append((i * FRAME_SECONDS, j * FRAME_SECONDS))
            i = j
        else:
            i += 1
    return start, end, gaps


def _baseline(units: Sequence[Unit], start: float, end: float) -> Tuple[List[float], List[float]]:
    """(start time of each word, end time of each word) spreading [start, end] by length and pause room."""
    total = sum(_weight(w) + PAUSE_WEIGHT[k] for i, (w, k) in enumerate(units) if i < len(units) - 1)
    total += _weight(units[-1][0])
    scale = (end - start) / total if total else 0.0
    starts, ends, at = [], [], start
    for i, (word, kind) in enumerate(units):
        starts.append(at)
        at += _weight(word) * scale
        ends.append(at)
        if i < len(units) - 1:
            at += PAUSE_WEIGHT[kind] * scale
    return starts, ends


def _match_pauses(expected: List[float], found: List[Tuple[float, float]], tolerance: float) -> List[Tuple[int, int]]:
    """Pair expected pauses (by predicted time) with real ones, in order, as a cheapest alignment."""
    skip = 0.6
    n, m = len(expected), len(found)
    centres = [(a + b) / 2 for a, b in found]
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0], back[i][0] = i * skip, "e"
    for j in range(1, m + 1):
        cost[0][j], back[0][j] = j * skip, "g"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, how = cost[i - 1][j] + skip, "e"
            if cost[i][j - 1] + skip < best:
                best, how = cost[i][j - 1] + skip, "g"
            distance = abs(expected[i - 1] - centres[j - 1])
            if distance <= tolerance and cost[i - 1][j - 1] + distance < best:
                best, how = cost[i - 1][j - 1] + distance, "m"
            cost[i][j], back[i][j] = best, how
    pairs, i, j = [], n, m
    while i > 0 or j > 0:
        how = back[i][j]
        if how == "m":
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif how == "e":
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def word_times(units: Sequence[Unit], start: float, end: float, gaps: Sequence[Tuple[float, float]] = ()) -> List[float]:
    """Start time of every word, fitted to the real pauses in the audio where they can be found."""
    if not units:
        return []
    starts, ends = _baseline(units, start, end)
    if not gaps or len(units) < 2:
        return starts

    pause_at = [i for i, (_, kind) in enumerate(units[:-1]) if PAUSE_WEIGHT[kind] > 0]
    tolerance = 1.0 + 0.12 * (end - start)
    pairs = _match_pauses([ends[i] for i in pause_at], list(gaps), tolerance)
    if not pairs:
        return starts

    # Cut the words into stretches at the matched pauses; each stretch gets exactly the time between them.
    cuts = [(pause_at[e], gaps[g]) for e, g in pairs]
    times = [0.0] * len(units)
    first_word, stretch_start = 0, start
    for word_index, (gap_start, gap_end) in cuts + [(len(units) - 1, (end, end))]:
        last_word = word_index
        stretch = units[first_word:last_word + 1]
        # inside a stretch every unmatched pause still takes its room; the pause at its end is the real gap
        inner = [(w, k if i < len(stretch) - 1 else "") for i, (w, k) in enumerate(stretch)]
        inner_starts, _ = _baseline(inner, stretch_start, gap_start if word_index != len(units) - 1 else end)
        times[first_word:last_word + 1] = inner_starts
        first_word, stretch_start = last_word + 1, gap_end
    return times


def build_timings(title_words: int, units: Sequence[Unit], times: Sequence[float], duration: float, shift: float = MP3_DELAY) -> dict:
    """The JSON the page uses: words, their start times (seconds into the played MP3), the length."""
    return {
        "v": 1,
        "title": title_words,
        "w": [w for w, _ in units],
        "t": [round(t + shift, 3) for t in times],
        "d": round(duration + shift, 2),
    }


def estimate_timings(title: str, paragraphs, duration: float) -> Optional[dict]:
    """Timings from the text and the length of the audio alone (for narrations made without them)."""
    units = units_for_story(title, paragraphs)
    if not units or duration <= 0:
        return None
    # ``duration`` is the length of the played MP3, which already includes its delay.
    start = ESTIMATE_LEAD + MP3_DELAY
    end = max(start + 0.1, duration - ESTIMATE_TRAIL)
    return build_timings(len(title.split()), units, word_times(units, start, end), duration, shift=0.0)


def dumps(timings: dict) -> str:
    return json.dumps(timings, ensure_ascii=False, separators=(",", ":"))
