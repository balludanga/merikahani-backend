"""Which voices read which language.

Google's voice library has voices made for some languages (Hindi, Indian English, Tamil, Telugu,
and one woman and one man each for Bengali, Gujarati, Kannada, Malayalam, Marathi, Nepali,
Punjabi and Urdu). For the rest (Assamese, Kashmiri, Maithili, Manipuri, Odia, Santali, Sindhi)
there is none, so the built-in voices are used: the speech model detects the language from the
text, so any voice can read any language it lists.

Hindi is not here: its pools are the ``TTS_FEMALE_VOICES`` / ``TTS_MALE_VOICES`` settings, which
the owner chose by ear. Ids are the library's own (``GET /v1beta/voices?language_code=ta-IN``).

None of the other pools has been listened to by a native speaker: the personas below were
picked from the library's descriptions (storyteller, companion, podcaster, tutor), not by ear.
"""

from typing import Dict, List, Optional

# The language's own library voices, warm and conversational personas first.
_LIBRARY: Dict[str, Dict[str, List[str]]] = {
    "en": {  # Indian English
        "female": ["en-in-storyteller-1", "en-in-storyteller-3", "en-in-storyteller-5", "en-in-storyteller-8"],
        "male": ["en-in-storyteller-2", "en-in-storyteller-4", "en-in-storyteller-6", "en-in-storyteller-7"],
    },
    "ta": {
        "female": ["ta-in-advisor-6", "ta-in-assistant-1", "ta-in-assistant-3", "ta-in-assistant-4"],
        "male": ["ta-in-advisor-2", "ta-in-advisor-4", "ta-in-tutor-2", "ta-in-tutor-8"],
    },
    "te": {
        "female": ["te-in-assistant-5", "te-in-tutor-1", "te-in-tutor-4", "te-in-advisor-3"],
        "male": ["te-in-assistant-7", "te-in-assistant-8", "te-in-assistant-2", "te-in-tutor-7"],
    },
    # One woman and one man each: every story in the language is read by one of these two.
    "bn": {"female": ["bn-in-assistant-1"], "male": ["bn-in-podcaster-1"]},
    "gu": {"female": ["gu-in-techagent-1"], "male": ["gu-in-concierge-1"]},
    "kn": {"female": ["kn-in-tutor-1"], "male": ["kn-in-advisor-1"]},
    "ml": {"female": ["ml-in-podcaster-1"], "male": ["ml-in-concierge-1"]},
    "mr": {"female": ["mr-in-assistant-1"], "male": ["mr-in-assistant-2"]},
    "ne": {"female": ["ne-np-podcaster-1"], "male": ["ne-np-podcaster-2"]},
    "pa": {"female": ["pa-in-storyteller-1"], "male": ["pa-in-concierge-1"]},
    "ur": {"female": ["ur-pk-tutor-1"], "male": ["ur-pk-podcaster-1"]},
}

# Built-in voices (they speak every language the model lists): the storyteller and companion personas.
_BUILT_IN: Dict[str, List[str]] = {
    "female": ["achernar", "callirrhoe", "despina"],
    "male": ["algenib", "enceladus", "achird"],
}


def pools_for(language: Optional[str]) -> Optional[Dict[str, List[str]]]:
    """The female and male voices for a language, or None for Hindi (whose pools are settings).

    A language with no library voices of its own is read by the built-in ones.
    """
    if language in (None, "", "hi"):
        return None
    pools = _LIBRARY.get(language, _BUILT_IN)
    return {gender: list(voices) for gender, voices in pools.items()}
