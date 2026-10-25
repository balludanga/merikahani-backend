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
MAX_BLIP = 0.14  # a sound shorter than this between two silences (a click, a breath) does not split a pause

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
            if (j - i) * FRAME_SECONDS >= MIN_GAP or (gaps and (i * FRAME_SECONDS - gaps[-1][1]) < MAX_BLIP):
                # a pause, or a short quiet right after another (a click or breath inside one pause)
                if gaps and (i * FRAME_SECONDS - gaps[-1][1]) < MAX_BLIP:
                    gaps[-1] = (gaps[-1][0], j * FRAME_SECONDS)
                else:
                    gaps.append((i * FRAME_SECONDS, j * FRAME_SECONDS))
            i = j
        else:
            i += 1
    return start, end, gaps


# Costs for the assignment of words to the stretches of speech between the real pauses.
# A pause the punctuation did not predict is normal (a speaker also breathes at phrase breaks);
# a full stop with no pause at all is not.
BOUNDARY_COST = {"sentence": 0.0, "paragraph": 0.0, "turn": 0.0, "clause": 0.3, "": 1.0}
# How long the silence after a word of each kind typically is (seconds), measured on real narrations:
# a full stop or line break ~0.9 s, a comma ~0.55 s, a pause with no punctuation ~0.35 s. A silence of the
# wrong length for the word it would follow is a poor boundary.
EXPECTED_PAUSE = {"sentence": 0.9, "paragraph": 1.0, "turn": 0.6, "clause": 0.55, "": 0.35}
PAUSE_SPREAD = 0.4
PAUSE_LENGTH_WEIGHT = 1.0


def boundary_cost(kind: str, length: float) -> float:
    off = (length - EXPECTED_PAUSE[kind]) / PAUSE_SPREAD
    return BOUNDARY_COST[kind] + PAUSE_LENGTH_WEIGHT * min(off * off, 6.0)

MISSING_COST = {"sentence": 2.5, "paragraph": 2.5, "turn": 1.5, "clause": 0.8, "": 0.0}
# The price of ignoring a real pause that is not between two words.
UNUSED_GAP_COST = 2.0


MAX_UNUSED_GAPS = 5
_L = r"[\u0900-\u0963\u0966-\u097F\w]"
_SPLIT = re.compile(r"(?<=" + _L + r"[—–'’‘\"“”])(?=" + _L + r")")


def _pieces(units: Sequence[Unit]) -> List[Tuple[int, float, str]]:
    """(word index, letters, pause kind after it), a word split at a dash so a pause there can be a boundary."""
    pieces: List[Tuple[int, float, str]] = []
    for index, (word, kind) in enumerate(units):
        parts = [p for p in _SPLIT.split(word) if p]
        if len(parts) <= 1:
            pieces.append((index, _weight(word), kind))
            continue
        for number, part in enumerate(parts):
            pieces.append((index, _weight(part), kind if number == len(parts) - 1 else "clause"))
    return pieces


def _spread(letters: Sequence[float], kinds: Sequence[str], start: float, end: float) -> List[float]:
    """Start times of consecutive words spreading [start, end] by length, with room for the pauses between them."""
    weights = [w + (PAUSE_WEIGHT[k] if i < len(letters) - 1 else 0.0) for i, (w, k) in enumerate(zip(letters, kinds))]
    total = sum(weights)
    if total <= 0:
        return [start] * len(letters)
    times, at = [], start
    for weight in weights:
        times.append(at)
        at += (end - start) * weight / total
    return times


def _assign(letters: List[float], kinds: List[str], start: float, end: float, gaps: List[Tuple[float, float]]):
    """Cut the words into stretches, one per real pause that falls between words.

    Every real silence is a candidate boundary. The cheapest assignment is found by dynamic
    programming: a stretch of words should last about as long as its letters take at the story's
    speaking rate; boundaries should fall where punctuation expects a pause; a pause may be left
    unused. Returns [(first word, last word, stretch start, stretch end)], or None if nothing fits.
    """
    n, m = len(letters), len(gaps)
    cum = [0.0]
    pause_room = [0.0]
    missing = [0.0]
    for letter, kind in zip(letters, kinds):
        cum.append(cum[-1] + letter)
        pause_room.append(pause_room[-1] + PAUSE_WEIGHT[kind])
        missing.append(missing[-1] + MISSING_COST[kind])
    speech = max(0.1, (end - start) - sum(b - a for a, b in gaps))
    rate = cum[n] / speech  # letters a second while speaking
    elapsed, taken = [0.0] * (m + 1), 0.0  # speech time before each pause
    for j, (a, b) in enumerate(gaps, start=1):
        elapsed[j] = (a - start) - taken
        taken += b - a

    skipped = [0.0]  # skipped[j]: the price of ignoring pauses 1..j
    for a, b in gaps:
        skipped.append(skipped[-1] + UNUSED_GAP_COST)

    def stretch_from(j):  # where the stretch that follows pause j begins (j = 0: the start)
        return start if j == 0 else gaps[j - 1][1]

    def cost(first, last, t0, t1):
        expected = (cum[last + 1] - cum[first] + pause_room[last] - pause_room[first]) / rate
        sigma = 0.2 * expected + 0.3
        return min(((t1 - t0 - expected) / sigma) ** 2, 30.0) + missing[last] - missing[first]

    # words that could sit just before pause j: those whose proportional position is near it
    near = [[]] + [
        [i for i in range(n - 1) if abs(cum[i + 1] / rate - elapsed[j]) <= 2.5 + 0.12 * elapsed[j]] for j in range(1, m + 1)
    ]
    best = {(0, -1): (0.0, None)}
    for j in range(1, m + 1):
        for i in near[j]:
            top = None
            for jp in range(j - 1, max(-1, j - 2 - MAX_UNUSED_GAPS), -1):
                previous = [-1] if jp == 0 else near[jp]
                for ip in previous:
                    if ip >= i or (jp, ip) not in best:
                        continue
                    c = (best[(jp, ip)][0] + cost(ip + 1, i, stretch_from(jp), gaps[j - 1][0])
                         + boundary_cost(kinds[i], gaps[j - 1][1] - gaps[j - 1][0]) + skipped[j - 1] - skipped[jp])
                    if top is None or c < top[0]:
                        top = (c, (jp, ip))
            if top:
                best[(j, i)] = top
    final = None
    for jp in range(m, max(-1, m - 1 - MAX_UNUSED_GAPS), -1):
        for ip in ([-1] if jp == 0 else near[jp]):
            if (jp, ip) in best and ip < n - 1:
                c = best[(jp, ip)][0] + cost(ip + 1, n - 1, stretch_from(jp), end) + skipped[m] - skipped[jp]
                if final is None or c < final[0]:
                    final = (c, (jp, ip))
    if final is None:
        return None
    chain, key = [], final[1]
    while key is not None:
        chain.append(key)
        key = best[key][1]
    chain.reverse()  # (0, -1), then each used pause as (pause number, last word before it)
    groups = []
    for (jp, ip), (j, i) in zip(chain, chain[1:]):
        groups.append((ip + 1, i, stretch_from(jp), gaps[j - 1][0]))
    jp, ip = chain[-1]
    groups.append((ip + 1, n - 1, stretch_from(jp), end))
    return groups


def word_times(units: Sequence[Unit], start: float, end: float, gaps: Sequence[Tuple[float, float]] = ()) -> List[float]:
    """Start time of every word, fitted to the real pauses in the audio where they can be found."""
    if not units:
        return []
    pieces = _pieces(units)
    letters = [p[1] for p in pieces]
    kinds = [p[2] for p in pieces]
    usable = [g for g in gaps if g[0] > start and g[1] < end]
    groups = _assign(letters, kinds, start, end, usable) if usable and len(pieces) > 1 else None
    if groups is None:
        piece_times = _spread(letters, kinds, start, end)
    else:
        piece_times = [0.0] * len(pieces)
        for first, last, t0, t1 in groups:
            piece_times[first:last + 1] = _spread(letters[first:last + 1], kinds[first:last + 1], t0, t1)
    times: List[float] = []
    for piece, (word_index, _, _) in zip(piece_times, pieces):
        if word_index == len(times):  # the first piece of a word gives the word's time
            times.append(piece)
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
