"""Prepare a story for the speech model: who says what, in pieces a voice can read.

A story is a title, paragraphs of narration, and quoted speech. ``scan`` finds the quotes (the
code does this, never the language model, so the writer's words cannot be altered). ``casting``
decides the narrator's gender and who speaks each quote; ``build_requests`` then turns the
story into a list of requests, each read by one voice, with the silence to leave before it.
Pacing, pauses inside a request and feeling are the speech model's job (see the style texts).
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Callable, List

from app.core.config import settings

# Bumped whenever the preparation or the style instructions change, so cached audio is remade.
NARRATION_VERSION = "g3"

STYLE = (
    "Read this aloud in natural, native Hindi, as the author narrating their own story to a "
    "close friend: warm, unhurried and intimate. Let the pace and pitch follow the meaning. "
    "Breathe briefly at commas, pause longer between paragraphs, lift gently for questions, "
    "and slow down at tender or emotional moments. Never sound like a news reader or a voice "
    "assistant."
)

DIALOGUE_STYLE = (
    "Say this as a real person speaking in an ordinary conversation, in natural, native Hindi: "
    "with the feeling the words carry (affection, worry, anger, teasing), at an everyday "
    "speaking pace, not as narration. Never sound like a news reader or a voice assistant."
)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
# A line with none of these ("* * *", "———") is a scene divider, not speech.
_HAS_WORDS = re.compile(r"[ऄ-ॣ०-ॿ\w]")
_QUOTE = re.compile(r"“([^”]*)”|\"([^\"\n]*)\"")
_STRAY_QUOTES = re.compile(r"[“”\"]")
_SENTENCE_END = re.compile(r"(?<=[।॥?!])\s+|(?<=\.)\s+")

# Silence (seconds) left before a request that begins a new paragraph, and before one that
# continues the same paragraph (a line of dialogue after "उसने कहा").
PARAGRAPH_PAUSE = 0.7
TURN_PAUSE = 0.25


@dataclass
class Span:
    kind: str  # "narration" or "quote"
    text: str
    q: int = 0  # 1, 2, 3... for quotes, in reading order


@dataclass
class Turn:
    voice: str
    dialogue: bool
    text: str
    starts_paragraph: bool = False


@dataclass
class Window:
    """What one speech request reads: a run of turns by at most two voices."""

    turns: List[Turn]
    pause: float = 0.0  # silence before this window, in seconds

    @property
    def voices(self) -> List[str]:
        return list(dict.fromkeys(t.voice for t in self.turns))

    @property
    def chars(self) -> int:
        return sum(len(t.text) for t in self.turns)


def scan(content: str) -> List[List[Span]]:
    """The story as paragraphs of narration and numbered quotes."""
    paragraphs: List[List[Span]] = []
    counter = 0
    for raw in _EMOJI.sub("", content or "").split("\n"):
        line = re.sub(r"[^\S\n]+", " ", raw).strip()
        if not line or not _HAS_WORDS.search(line):
            continue
        spans: List[Span] = []
        pos = 0
        for match in _QUOTE.finditer(line):
            before = _STRAY_QUOTES.sub("", line[pos:match.start()]).strip()
            if before and _HAS_WORDS.search(before):
                spans.append(Span("narration", before))
            inner = (match.group(1) if match.group(1) is not None else match.group(2)).strip()
            if inner and _HAS_WORDS.search(inner):
                counter += 1
                spans.append(Span("quote", inner, counter))
            pos = match.end()
        rest = _STRAY_QUOTES.sub("", line[pos:]).strip()
        if rest and _HAS_WORDS.search(rest):
            spans.append(Span("narration", rest))
        if spans:
            paragraphs.append(spans)
    return paragraphs


def tagged_text(title: str, paragraphs: List[List[Span]]) -> str:
    """The story with every quote numbered ``[Q1]“...”``, for the model that labels speakers."""
    lines = [f"शीर्षक: {title}"] if title else []
    for spans in paragraphs:
        lines.append(" ".join(f"[Q{s.q}]“{s.text}”" if s.kind == "quote" else s.text for s in spans))
    return "\n".join(lines)


def _split_long(paragraph: str, limit: int) -> List[str]:
    pieces: List[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > limit:  # a sentence with no stop in sight: cut at a space
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def build_turns(
    title: str,
    paragraphs: List[List[Span]],
    narrator_voice: str,
    voice_for_quote: Callable[[int], str],
    limit: int = 0,
) -> List[Turn]:
    """The story as turns in reading order. ``voice_for_quote(q)`` is who speaks quote number q.

    Neighbouring pieces by the same voice in the same manner are joined, and a piece longer than
    ``limit`` (a long monologue) is cut at sentence ends.
    """
    limit = limit or settings.TTS_CHUNK_CHARS
    turns: List[Turn] = []
    heading = _STRAY_QUOTES.sub("", _EMOJI.sub("", title or "")).strip()

    def add(voice: str, dialogue: bool, text: str, new_paragraph: bool) -> None:
        last = turns[-1] if turns else None
        if last and last.voice == voice and last.dialogue == dialogue and len(last.text) + 2 + len(text) <= limit:
            last.text += ("\n\n" if new_paragraph else " ") + text
            return
        turns.append(Turn(voice, dialogue, text, new_paragraph))

    if heading:
        add(narrator_voice, False, heading, True)
    for spans in paragraphs:
        first = True
        for span in spans:
            voice = voice_for_quote(span.q) if span.kind == "quote" else narrator_voice
            add(voice, span.kind == "quote", span.text, first)
            first = False

    result: List[Turn] = []
    for turn in turns:
        if len(turn.text) <= limit:
            result.append(turn)
            continue
        for index, piece in enumerate(_split_long(turn.text.replace("\n\n", " "), limit)):
            result.append(Turn(turn.voice, turn.dialogue, piece, turn.starts_paragraph and index == 0))
    return result


def build_windows(turns: List[Turn], limit: int = 0, max_voices: int = 2) -> List[Window]:
    """Group turns into requests of at most ``max_voices`` voices and ``limit`` characters.

    Google reads two voices in one request, so a conversation costs one request, not one per line;
    the free tier allows only a handful of speech requests a day, so this matters.
    """
    limit = limit or settings.TTS_WINDOW_CHARS
    windows: List[Window] = []
    current: List[Turn] = []
    chars = 0
    for turn in turns:
        voices = list(dict.fromkeys([t.voice for t in current] + [turn.voice]))
        if current and (len(voices) > max_voices or chars + len(turn.text) > limit):
            windows.append(Window(current))
            current, chars = [], 0
        current.append(turn)
        chars += len(turn.text)
    if current:
        windows.append(Window(current))
    for window in windows[1:]:
        window.pause = PARAGRAPH_PAUSE if window.turns[0].starts_paragraph else TURN_PAUSE
    return windows


def fingerprint(title: str, content: str) -> str:
    """Identifies this exact audio: same text, models, voice pools and reading styles."""
    parts = [
        NARRATION_VERSION, settings.TTS_MODELS, settings.CAST_MODELS,
        settings.TTS_FEMALE_VOICES, settings.TTS_MALE_VOICES, STYLE, DIALOGUE_STYLE,
        title or "", content or "",
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def spoken_chars(windows: List[Window]) -> int:
    return sum(w.chars for w in windows)
