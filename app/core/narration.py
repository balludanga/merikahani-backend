"""Turn a story's plain text into SSML paced the way a writer reads their own work aloud.

Same rules as the browser fallback (frontend ``planNarration`` in ``utils/ssmlTTS.js``): a
breath at every comma, longer at a full stop, longer still between paragraphs and at a blank
line or "* * *" divider; questions lift, ellipses trail off, quoted speech is set a touch
higher and brisker than the narration, and each sentence gets a few percent of its own speed
and pitch so the voice is not a metronome. The variation comes from a hash of the sentence,
so the same story always produces the same audio (it is cached by that).
"""

import re
from typing import Dict, List
from xml.sax.saxutils import escape

PAUSE = {
    "clause": 170,
    "sentence": 400,
    "question": 460,
    "trail": 620,
    "paragraph": 750,
    "section": 1300,
    "title": 1000,
}

# Bumped whenever the pacing changes, so cached audio is made again.
NARRATION_VERSION = "1"

BASE_RATE = 0.9
BASE_PITCH = 1.0

_QUOTE_MARKS = re.compile(r"[“”\"‘’]")
# A line with none of these ("* * *", "———") is a scene break, not speech.
_HAS_WORDS = re.compile(r"[ऄ-ॣ०-ॿ\w]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
_SENTENCES = re.compile(r"(.+?(?:[।॥?!]+[”\"’]*|\.[”\"’]*(?=\s|$)|$))\s*")
_CLAUSES = re.compile(r"[^,;:—–…“”\"‘’]+(?:[,;:—–…“”\"‘’]+|$)")
_QUESTION_END = re.compile(r"[?？]\s*[”\"’]*\s*$")
_EXCLAIM_END = re.compile(r"!\s*[”\"’]*\s*$")
_TRAIL_END = re.compile(r"(…|\.{2,})\s*[”\"’]*\s*$")

Segment = Dict[str, object]


def _hash_of(text: str) -> int:
    h = 0
    for ch in text:
        h = (h * 31 + ord(ch)) & 0x7FFFFFFF
    return h


def plan_narration(text: str, title: str = "") -> List[Segment]:
    """Speech pieces ``{text, rate, pitch}`` and silences ``{pause: ms}``, in order."""
    segments: List[Segment] = []

    def pause(ms: int) -> None:
        if not segments:
            return
        last = segments[-1]
        if "pause" in last:
            last["pause"] = max(int(last["pause"]), ms)
        else:
            segments.append({"pause": ms})

    def say(spoken: str, rate: float, pitch: float) -> None:
        segments.append({"text": spoken, "rate": rate, "pitch": pitch})

    heading = _QUOTE_MARKS.sub("", _EMOJI.sub("", title or "")).strip()
    if heading:
        say(heading, BASE_RATE * 0.94, BASE_PITCH * 0.97)
        pause(PAUSE["title"])

    in_quote = False
    blank_lines = 0

    for raw in _EMOJI.sub("", text or "").split("\n"):
        line = raw.strip()
        if not line:
            blank_lines += 1
            continue
        if not _HAS_WORDS.search(line):
            pause(PAUSE["section"])
            blank_lines = 0
            continue
        pause(PAUSE["section"] if blank_lines else PAUSE["paragraph"])
        blank_lines = 0

        for sentence in _SENTENCES.findall(line):
            clauses = _CLAUSES.findall(sentence) or [sentence]
            parts: List[str] = []
            buffer = ""
            for clause in clauses:
                buffer += clause
                # Never merge across a quote mark: dialogue keeps its own voice.
                if len(buffer.strip()) >= 14 or clause.rstrip()[-1:] in "“”\"‘’":
                    parts.append(buffer)
                    buffer = ""
            if buffer.strip():
                if parts:
                    parts[-1] += buffer
                else:
                    parts.append(buffer)

            for index, part in enumerate(parts):
                is_last = index == len(parts) - 1
                starts_in_quote = in_quote or bool(re.match(r"\s*[“\"‘]", part))
                for ch in part:
                    if ch in "“‘":
                        in_quote = True
                    elif ch in "”’":
                        in_quote = False
                    elif ch == '"':
                        in_quote = not in_quote

                spoken = _QUOTE_MARKS.sub("", part).strip()
                if not spoken or not _HAS_WORDS.search(spoken):
                    continue

                ending = "clause"
                if _TRAIL_END.search(part):
                    ending = "trail"
                elif is_last:
                    if _QUESTION_END.search(part):
                        ending = "question"
                    elif _EXCLAIM_END.search(part):
                        ending = "exclaim"
                    else:
                        ending = "sentence"

                h = _hash_of(spoken)
                rate = BASE_RATE * (1 + ((h % 7) - 3) / 100)
                pitch = BASE_PITCH * (1 + (((h >> 3) % 5) - 2) / 100)
                if starts_in_quote:
                    pitch *= 1.05
                    rate *= 1.03
                if ending == "question":
                    pitch *= 1.05
                elif ending == "exclaim":
                    pitch *= 1.04
                elif ending == "trail":
                    rate *= 0.92

                say(spoken, rate, pitch)
                pause(PAUSE["question" if ending == "exclaim" else ending])

    while segments and "pause" in segments[-1]:
        segments.pop()
    return segments


def spoken_length(segments: List[Segment]) -> int:
    return sum(len(str(s["text"])) for s in segments if "text" in s)


def _percent(factor: float) -> str:
    return f"{round((factor - 1) * 100):+d}%"


def to_ssml_chunks(segments: List[Segment], voice: str, max_chars: int = 2500) -> List[str]:
    """SSML documents of at most ``max_chars`` spoken characters each (one request apiece)."""
    chunks: List[str] = []
    body: List[str] = []
    size = 0

    def flush() -> None:
        nonlocal body, size
        if body:
            chunks.append(
                '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="hi-IN">'
                f'<voice name="{escape(voice)}">{"".join(body)}</voice></speak>'
            )
        body, size = [], 0

    for segment in segments:
        if "pause" in segment:
            body.append(f'<break time="{int(segment["pause"])}ms"/>')
            continue
        spoken = str(segment["text"])
        if size and size + len(spoken) > max_chars:
            flush()
        body.append(
            f'<prosody rate="{_percent(float(segment["rate"]))}" '
            f'pitch="{_percent(float(segment["pitch"]))}">{escape(spoken)}</prosody>'
        )
        size += len(spoken)
    flush()
    return chunks
