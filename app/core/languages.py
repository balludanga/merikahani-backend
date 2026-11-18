"""The languages a story can be written in: the 22 languages of the Eighth Schedule, and English.

A story carries one language code (``posts.language``). A reader chooses the languages they read
(``users.read_languages_json``) and a writer the ones they write in (``write_languages_json``);
the feed is filtered by the first. Nothing here limits what is written: the code only labels it, so
that the page can set the right ``lang`` / font / direction, search engines are told the real
language, and a reviewer can see which stories they are able to read.

Codes are the shortest ISO 639 code (BCP 47): two letters where one exists, else three.
The same list, in the same order, is in the frontend (``src/languages/registry.js``); a test on
each side pins the codes, so the two cannot drift apart unnoticed.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_LANGUAGE = "hi"


@dataclass(frozen=True)
class Language:
    code: str
    english: str
    hindi: str  # the name in Devanagari: the site's own words are Devanagari only
    native: str  # the name as its own speakers write it (shown in brackets when it is not Devanagari)
    scripts: Tuple[str, ...]  # ISO 15924 codes, the usual one first
    stop: str  # the character that ends a sentence
    og_locale: str

    @property
    def script(self) -> str:
        return self.scripts[0]

    @property
    def rtl(self) -> bool:
        return self.script == "Arab"

    def as_dict(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "english": self.english,
            "hindi": self.hindi,
            "native": self.native,
            "scripts": list(self.scripts),
            "rtl": self.rtl,
            "stop": self.stop,
            "og_locale": self.og_locale,
        }


# Hindi first (the site's home language), English second, then the other 21 by name.
LANGUAGES: Tuple[Language, ...] = (
    Language("hi", "Hindi", "हिंदी", "हिन्दी", ("Deva",), "।", "hi_IN"),
    Language("en", "English", "अंग्रेज़ी", "English", ("Latn",), ".", "en_IN"),
    Language("as", "Assamese", "असमिया", "অসমীয়া", ("Beng",), "।", "as_IN"),
    Language("bn", "Bengali", "बांग्ला", "বাংলা", ("Beng",), "।", "bn_IN"),
    Language("brx", "Bodo", "बोडो", "बरʼ", ("Deva",), "।", "brx_IN"),
    Language("doi", "Dogri", "डोगरी", "डोगरी", ("Deva",), "।", "doi_IN"),
    Language("gu", "Gujarati", "गुजराती", "ગુજરાતી", ("Gujr",), ".", "gu_IN"),
    Language("kn", "Kannada", "कन्नड़", "ಕನ್ನಡ", ("Knda",), ".", "kn_IN"),
    Language("ks", "Kashmiri", "कश्मीरी", "کٲشُر", ("Arab", "Deva"), "۔", "ks_IN"),
    Language("kok", "Konkani", "कोंकणी", "कोंकणी", ("Deva",), "।", "kok_IN"),
    Language("mai", "Maithili", "मैथिली", "मैथिली", ("Deva",), "।", "mai_IN"),
    Language("ml", "Malayalam", "मलयालम", "മലയാളം", ("Mlym",), ".", "ml_IN"),
    Language("mni", "Manipuri (Meitei)", "मणिपुरी", "ꯃꯩꯇꯩꯂꯣꯟ", ("Mtei", "Beng"), "꯫", "mni_IN"),
    Language("mr", "Marathi", "मराठी", "मराठी", ("Deva",), ".", "mr_IN"),
    Language("ne", "Nepali", "नेपाली", "नेपाली", ("Deva",), "।", "ne_NP"),
    Language("or", "Odia", "ओड़िया", "ଓଡ଼ିଆ", ("Orya",), "।", "or_IN"),
    Language("pa", "Punjabi", "पंजाबी", "ਪੰਜਾਬੀ", ("Guru",), "।", "pa_IN"),
    Language("sa", "Sanskrit", "संस्कृत", "संस्कृतम्", ("Deva",), "।", "sa_IN"),
    Language("sat", "Santali", "संताली", "ᱥᱟᱱᱛᱟᱲᱤ", ("Olck", "Deva"), "᱾", "sat_IN"),
    Language("sd", "Sindhi", "सिंधी", "سِنڌِي", ("Arab", "Deva"), "۔", "sd_PK"),
    Language("ta", "Tamil", "तमिल", "தமிழ்", ("Taml",), ".", "ta_IN"),
    Language("te", "Telugu", "तेलुगु", "తెలుగు", ("Telu",), ".", "te_IN"),
    Language("ur", "Urdu", "उर्दू", "اردو", ("Arab",), "۔", "ur_PK"),
)

BY_CODE: Dict[str, Language] = {language.code: language for language in LANGUAGES}
CODES: Tuple[str, ...] = tuple(language.code for language in LANGUAGES)
MAX_LANGUAGES = len(CODES)

# The ways people write a code that are not the code itself (a locale, a capital letter, a stray space).
_ALIASES = {"hin": "hi", "eng": "en", "ben": "bn", "tam": "ta", "tel": "te", "mar": "mr", "guj": "gu", "kan": "kn",
            "mal": "ml", "pan": "pa", "urd": "ur", "ori": "or", "odi": "or", "asm": "as", "nep": "ne", "san": "sa",
            "snd": "sd", "kas": "ks", "mni-mtei": "mni", "gom": "kok"}


def normalize_language(value: Optional[str]) -> str:
    """The code for what a caller sent ("HI", " ta-IN ", "hin"), or ValueError if it is not one of ours."""
    text = (value or "").strip().lower().replace("_", "-")
    code = text if text in BY_CODE else _ALIASES.get(text)
    if code is None and "-" in text:
        head = text.split("-", 1)[0]
        code = head if head in BY_CODE else _ALIASES.get(head)
    if code is None:
        raise ValueError("यह भाषा उपलब्ध नहीं है")
    return code


def clean_language_list(values: Optional[Iterable[str]]) -> List[str]:
    """A person's chosen languages: known codes only, no repeats, in the order given."""
    out: List[str] = []
    for value in values or []:
        code = normalize_language(value)
        if code not in out:
            out.append(code)
    return out


def parse_filter(value: Optional[str]) -> Optional[List[str]]:
    """``?lang=ta,hi`` for a feed: the known codes in it. None when nothing was asked for; an empty
    list when something was asked for but none of it is a language we have (such a feed is empty)."""
    if value is None or not value.strip():
        return None
    codes: List[str] = []
    for part in value.split(","):
        try:
            code = normalize_language(part)
        except ValueError:
            continue
        if code not in codes:
            codes.append(code)
    return codes


def og_locale(code: Optional[str]) -> str:
    language = BY_CODE.get(code or "")
    return language.og_locale if language else BY_CODE[DEFAULT_LANGUAGE].og_locale


def html_lang(code: Optional[str]) -> str:
    return code if code in BY_CODE else DEFAULT_LANGUAGE


def tts_languages(setting: str) -> List[str]:
    """Languages whose narration the server may make (the ``TTS_LANGUAGES`` setting, comma separated)."""
    out: List[str] = []
    for part in (setting or "").split(","):
        try:
            code = normalize_language(part)
        except ValueError:
            continue
        if code not in out:
            out.append(code)
    return out


# Letters of every supported script, for patterns that must treat a word as one piece. Python's \w
# leaves out combining vowel signs (matras), which every Indic script has. Sentence punctuation
# (danda, the Arabic full stop and comma) is left out on purpose.
LETTER_RANGES = (
    "ऀ-ॣ०-෿"  # Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam
    "؀-؋؍-ؚ؜-؞ؠ-ۓە-ۿݐ-ݿࢠ-ࣿ"  # Arabic script
    "ﭐ-﷿ﹰ-﻿"  # Arabic presentation forms
    "᱐-ᱹ"  # Ol Chiki letters and digits (not its punctuation)
    "ꯀ-ꯪ꯬-꯹ꫠ-꫶"  # Meetei Mayek letters and digits (not its full stop)
)
