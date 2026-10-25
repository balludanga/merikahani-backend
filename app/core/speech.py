"""Azure AI Speech text-to-speech over plain REST (no SDK, so nothing extra to install).

Docs: https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech
One request returns at most 10 minutes of audio, so the caller sends a long story as several
SSML documents; MP3 frames of the same format can simply be joined end to end.
"""

import re
from typing import List

import requests

from app.core.config import settings

# 24 kHz / 48 kbps mono MP3: clear speech at about 360 KB a minute.
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
REQUEST_TIMEOUT = 60


class SpeechUnavailable(Exception):
    """The speech service could not produce audio (bad key, quota used up, outage)."""


def is_configured() -> bool:
    return bool(settings.AZURE_SPEECH_KEY)


def synthesize(ssml_chunks: List[str]) -> bytes:
    region = settings.AZURE_SPEECH_REGION
    if not re.fullmatch(r"[a-z0-9]+", region or ""):
        raise SpeechUnavailable("invalid region")
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": settings.AZURE_SPEECH_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "lekhani-narration",
    }
    audio = bytearray()
    for ssml in ssml_chunks:
        try:
            response = requests.post(url, headers=headers, data=ssml.encode("utf-8"), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise SpeechUnavailable(f"network: {type(exc).__name__}") from exc
        if response.status_code != 200:
            # 401/403 = key or region wrong, 429 = free quota or rate used up, 5xx = their outage.
            raise SpeechUnavailable(f"azure status {response.status_code}")
        audio.extend(response.content)
    return bytes(audio)
