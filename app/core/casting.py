"""Who tells the story, and who speaks: picking a voice for each.

A text model reads the story once and answers two small questions: whose point of view is it
(a woman's, a man's, or unclear), and which character says each numbered quote. Hindi helps
here: verbs and adjectives agree with the speaker's gender (गई / गया, बोली / बोला), so a
first-person narrator's gender is usually plain from the text. The model never returns the
story itself, only labels, so it cannot change the writer's words.

Voices are then drawn at random from the configured pools, matching each person's gender and
different from one another where the pool allows. The draw is seeded by the story's id, so a
story always gets the same voices (and a different story usually gets different ones).
"""

import hashlib
import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

from app.core.config import settings
from app.core.narration import Span, Window, build_turns, build_windows, scan, tagged_text
from app.core.speech import SpeechUnavailable, generate_json

logger = logging.getLogger(__name__)

GENDERS = ("female", "male")
# Used only if both pools are emptied by mistake in the settings.
FALLBACK_VOICES = {"female": ["Sulafat"], "male": ["Achird"]}

PROMPT = """You are helping a text-to-speech narrator read a Hindi story aloud with the right voices.
The story is below. Every piece of quoted speech is tagged [Q1], [Q2], ... in reading order.

Answer with JSON only:
- narrator_gender: "female" if the story is told from a woman's or girl's point of view, "male"
  if from a man's or boy's, otherwise "unknown". For a first-person story ("मैं") judge by the
  narrator's own verb and adjective forms (गई, रही, थी, अकेली = female; गया, रहा, था, अकेला =
  male). For a third-person story use the main character whose thoughts and feelings we follow.
  Use "unknown" if it is really not clear.
- speakers: every distinct character who speaks in quotes: id ("S1", "S2", ...), name as written in
  the story (Devanagari), gender ("female", "male" or "unknown"). Do not list the narrator here.
- quotes: for every tag number, who says it: a speaker id, or "narrator" when the first-person
  narrator says it. Use speech verbs and their gender (बोली / बोला), names, forms of address (माँ,
  भैया, बेटा), who is being spoken to, and the back-and-forth of a conversation.

Do not rewrite or repeat the story.

"""

_GENDER = {"type": "STRING", "enum": ["female", "male", "unknown"]}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "narrator_gender": _GENDER,
        "speakers": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"id": {"type": "STRING"}, "name": {"type": "STRING"}, "gender": _GENDER},
                "required": ["id", "name", "gender"],
            },
        },
        "quotes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"q": {"type": "INTEGER"}, "speaker": {"type": "STRING"}},
                "required": ["q", "speaker"],
            },
        },
    },
    "required": ["narrator_gender", "speakers", "quotes"],
}


@dataclass
class Speaker:
    id: str
    name: str
    gender: str


@dataclass
class Cast:
    narrator_gender: str = "unknown"
    speakers: Dict[str, Speaker] = field(default_factory=dict)
    quote_speaker: Dict[int, str] = field(default_factory=dict)  # quote number -> speaker id / "narrator"


def _clean(data: dict, quote_count: int) -> Cast:
    """Trust nothing in the model's answer: keep only well-formed labels."""
    cast = Cast()
    if isinstance(data, dict):
        if data.get("narrator_gender") in GENDERS:
            cast.narrator_gender = data["narrator_gender"]
        for item in data.get("speakers") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] != "narrator":
                gender = item.get("gender") if item.get("gender") in GENDERS else "unknown"
                cast.speakers[item["id"]] = Speaker(item["id"], str(item.get("name") or "")[:60], gender)
        for item in data.get("quotes") or []:
            if not isinstance(item, dict) or not isinstance(item.get("q"), int):
                continue
            who = item.get("speaker")
            if 1 <= item["q"] <= quote_count and (who == "narrator" or who in cast.speakers):
                cast.quote_speaker[item["q"]] = who
    return cast


def analyze(title: str, paragraphs: List[List[Span]]) -> Cast:
    """Read the story's cast. If the model cannot be reached the story is simply read by one voice."""
    quote_count = sum(1 for spans in paragraphs for s in spans if s.kind == "quote")
    try:
        data = generate_json(PROMPT + tagged_text(title, paragraphs), SCHEMA)
    except SpeechUnavailable as exc:
        logger.warning("could not read the story's cast (%s); using one voice", exc)
        return Cast()
    return _clean(data, quote_count)


def _pools() -> Dict[str, List[str]]:
    def split(value: str) -> List[str]:
        return [v.strip() for v in (value or "").split(",") if v.strip()]

    pools = {"female": split(settings.TTS_FEMALE_VOICES), "male": split(settings.TTS_MALE_VOICES)}
    for gender in GENDERS:
        if not pools[gender]:
            pools[gender] = pools["male" if gender == "female" else "female"] or FALLBACK_VOICES[gender]
    return pools


def assign_voices(cast: Cast, seed: int) -> Tuple[str, Callable[[int], str]]:
    """(narrator's voice, function giving the voice of quote number q)."""
    rng = random.Random(seed)
    pools = _pools()
    used: List[str] = []

    def pick(gender: str) -> str:
        options = [v for v in pools[gender] if v not in used] or pools[gender]
        voice = rng.choice(options)
        used.append(voice)
        return voice

    narrator_gender = cast.narrator_gender
    if narrator_gender not in GENDERS:
        # Unclear whose story it is: if everyone who speaks is a woman (or everyone a man), it
        # is most likely their story; otherwise leave it to chance.
        genders = {sp.gender for sp in cast.speakers.values()}
        if len(genders) == 1 and genders <= set(GENDERS):
            narrator_gender = genders.pop()
        else:
            narrator_gender = rng.choice(GENDERS)
    narrator_voice = pick(narrator_gender)
    speaker_voice: Dict[str, str] = {}
    for speaker_id, speaker in cast.speakers.items():
        speaker_voice[speaker_id] = pick(speaker.gender if speaker.gender in GENDERS else rng.choice(GENDERS))

    def voice_for_quote(q: int) -> str:
        return speaker_voice.get(cast.quote_speaker.get(q, "narrator"), narrator_voice)

    return narrator_voice, voice_for_quote


def seed_for(post_id: int) -> int:
    return int(hashlib.sha256(f"lekhani-voice-{post_id}".encode()).hexdigest()[:8], 16)


def plan_story(post_id: int, title: str, content: str) -> Tuple[List[Window], Cast]:
    """The requests that read this story (each at most two voices), and the cast behind them."""
    paragraphs = scan(content)
    cast = analyze(title, paragraphs)
    narrator_voice, voice_for_quote = assign_voices(cast, seed_for(post_id))
    turns = build_turns(title, paragraphs, narrator_voice, voice_for_quote)
    return build_windows(turns), cast
