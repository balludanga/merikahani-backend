"""Google Gemini over plain REST (free AI Studio key): speech, and the text model that casts it.

Speech docs: https://ai.google.dev/gemini-api/docs/speech-generation (Interactions API). The
model returns 24 kHz mono 16-bit WAV, about 3 MB a minute, so the pieces of a story are joined
as raw audio and compressed once to a 48 kbps MP3 (about 0.35 MB a minute) before storing.

Everything that can go wrong (no key, free quota used, outage, an answer in an unexpected
shape) ends as ``SpeechUnavailable``; the page then uses the reader's own browser voice.
"""

import base64
import io
import json
import logging
import threading
import time
import wave
from typing import Any, Callable, Dict, List, Optional, Tuple

import lameenc
import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
VOICES_URL = "https://generativelanguage.googleapis.com/v1beta/voices"
SAMPLE_RATE = 24000
REQUEST_TIMEOUT = 120
# Reading a story's cast is a small question; a model that has not answered in this long is stuck,
# and the next one should be asked instead of waiting out the full request timeout.
CAST_TIMEOUT = 25
# How long to stand down after Google says the free quota is used up, so a busy day does
# not turn every listener's tap into a failing request.
QUOTA_COOLDOWN = 15 * 60
MAX_RETRY_WAIT = 25

_model_blocked: Dict[str, float] = {}  # speech model -> when its daily allowance returns
_state_lock = threading.Lock()


class SpeechUnavailable(Exception):
    """The speech service could not produce audio right now."""


class RateLimited(SpeechUnavailable):
    """Google refused for a long while (a used-up daily allowance); ``wait`` is in seconds."""

    def __init__(self, wait: float) -> None:
        super().__init__(f"rate limit, back in {int(wait)}s")
        self.wait = wait


class Refused(SpeechUnavailable):
    """A wrong key, model or voice (HTTP 400/401/403/404)."""


def tts_models() -> List[str]:
    return [m.strip() for m in settings.TTS_MODELS.split(",") if m.strip()]


def _block_model(model: str, seconds: float) -> None:
    with _state_lock:
        _model_blocked[model] = time.time() + max(60.0, min(seconds, 24 * 3600))


def _blocked(model: str) -> bool:
    with _state_lock:
        return _model_blocked.get(model, 0.0) > time.time()


def is_configured() -> bool:
    return bool(settings.GEMINI_API_KEY)


def is_available() -> bool:
    """A key is set and at least one speech model still has allowance today."""
    return is_configured() and any(not _blocked(m) for m in tts_models())


def reset_block() -> None:
    with _state_lock:
        _model_blocked.clear()


def _post(url: str, body: dict, attempts: int = 3, timeout: float = 0) -> dict:
    """POST to Gemini; brief rate limits are waited out, a long one raises RateLimited."""
    if not is_configured():
        raise SpeechUnavailable("no key")
    headers = {"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"}
    for attempt in range(attempts):
        try:
            response = requests.post(url, json=body, headers=headers, timeout=timeout or REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise SpeechUnavailable(f"network: {type(exc).__name__}") from exc

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as exc:
                raise SpeechUnavailable("unreadable answer") from exc

        if response.status_code == 429 or response.status_code >= 500:
            try:
                wait = float(response.headers.get("Retry-After") or 8 * (attempt + 1))
            except ValueError:
                wait = 8 * (attempt + 1)
            if response.status_code == 429 and wait > MAX_RETRY_WAIT:
                raise RateLimited(wait)
            if attempt < attempts - 1:
                time.sleep(min(wait, MAX_RETRY_WAIT))
                continue
            if response.status_code == 429:
                raise RateLimited(QUOTA_COOLDOWN)
            raise SpeechUnavailable(f"status {response.status_code}")

        # 400 / 401 / 403 / 404: a wrong key, model or voice. Log what Google said (never the key).
        logger.warning("gemini request refused (%s): %s", response.status_code, response.text[:300])
        raise Refused(f"status {response.status_code}")
    raise SpeechUnavailable("gave up")


def _find_audio(node: Any) -> Optional[Tuple[str, str]]:
    """First (mime_type, base64 data) pair anywhere in the answer.

    The documented place is ``steps[].content[].data``; searching the whole tree keeps this
    working if the answer is nested a little differently.
    """
    if isinstance(node, dict):
        data = node.get("data")
        mime = str(node.get("mime_type") or node.get("mimeType") or "")
        if isinstance(data, str) and len(data) > 100 and (not mime or mime.startswith("audio")):
            return mime, data
        for value in node.values():
            found = _find_audio(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_audio(value)
            if found:
                return found
    return None


def _pcm_from(mime: str, raw: bytes) -> bytes:
    """16-bit mono PCM from the model's audio: a WAV file, or headerless L16 when streamed."""
    if raw[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(raw)) as w:
                if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != SAMPLE_RATE:
                    raise SpeechUnavailable("unexpected audio format")
                return w.readframes(w.getnframes())
        except wave.Error as exc:
            raise SpeechUnavailable("unreadable audio") from exc
    if "l16" in mime.lower() or not mime:
        return raw
    raise SpeechUnavailable(f"unsupported audio type {mime}")


def _window_body(model: str, window, narration_style: str, dialogue_style: str) -> dict:
    """The request that reads one window: one voice as plain text, two voices as a conversation."""
    voices = window.voices
    if len(voices) == 1:
        text = ""
        for index, turn in enumerate(window.turns):
            text += ("" if index == 0 else ("\n\n" if turn.starts_paragraph else " ")) + turn.text
        style = dialogue_style if all(t.dialogue for t in window.turns) else narration_style
        content = [{"type": "text", "text": text, "annotations": [{"type": "speech_metadata", "style": style}]}]
        speech_config: Any = [{"voice": voices[0]}]
    else:
        labels: Dict[str, str] = {}
        for voice in voices:
            narrates = any(t.voice == voice and not t.dialogue for t in window.turns)
            labels[voice] = "Narrator" if narrates and "Narrator" not in labels.values() else f"Character{len(labels) + 1}"
        content = [
            {
                "type": "text",
                "text": turn.text,
                "annotations": [
                    {
                        "type": "speech_metadata",
                        "speaker": labels[turn.voice],
                        "style": dialogue_style if turn.dialogue else narration_style,
                    }
                ],
            }
            for turn in window.turns
        ]
        speech_config = {"speakers": [{"speaker": labels[v], "voice": v} for v in voices]}
    return {
        "model": model,
        "input": [{"type": "user_input", "content": content}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": speech_config},
    }


def synthesize_window(window, narration_style: str, dialogue_style: str, prefer: Optional[str] = None) -> Tuple[bytes, str]:
    """(raw 16-bit 24 kHz mono audio, model used) for one window.

    Models whose daily allowance is used up are skipped; ``prefer`` (the model that read the
    previous window) goes first so one story keeps one sound where it can.
    """
    models = tts_models()
    if prefer in models:
        models = [prefer] + [m for m in models if m != prefer]
    last: Optional[Exception] = None
    for model in models:
        if _blocked(model):
            continue
        try:
            answer = _post(API_URL, _window_body(model, window, narration_style, dialogue_style))
        except RateLimited as exc:
            _block_model(model, exc.wait)
            last = exc
            continue
        except Refused as exc:
            last = exc  # this model does not take the request (e.g. a voice it lacks): try the next
            continue
        found = _find_audio(answer)
        if not found:
            logger.warning("speech answer had no audio: %s", json.dumps(answer)[:300])
            last = SpeechUnavailable("no audio in answer")
            continue
        mime, data = found
        return _pcm_from(mime, base64.b64decode(data)), model
    raise last or RateLimited(QUOTA_COOLDOWN)


def generate_json(prompt: str, schema: dict) -> Any:
    """Ask a text model for JSON that follows ``schema`` (used to read a story's cast).

    The models in ``CAST_MODELS`` are tried in turn: a busy one (503) or one that is gone (404)
    just hands over to the next. Raises SpeechUnavailable only if none of them answers.
    """
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0},
    }
    last: Optional[Exception] = None
    for model in [m.strip() for m in settings.CAST_MODELS.split(",") if m.strip()]:
        try:
            answer = _post(f"{MODELS_URL}/{model}:generateContent", body, attempts=1, timeout=CAST_TIMEOUT)
            return json.loads(answer["candidates"][0]["content"]["parts"][0]["text"])
        except SpeechUnavailable as exc:
            last = exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("cast answer from %s unreadable", model)
            last = SpeechUnavailable("unreadable cast")
            last.__cause__ = exc
    raise last or SpeechUnavailable("no text model configured")


def pcm_to_mp3(pcm: bytes) -> bytes:
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(48)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return bytes(encoder.encode(pcm)) + bytes(encoder.flush())


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def synthesize(
    windows: list,
    narration_style: str,
    dialogue_style: str,
    progress: Optional[Callable[[int, int], None]] = None,
    cache: Optional[Dict[int, bytes]] = None,
) -> bytes:
    """The whole story as one MP3. Each window is read by its voice(s), with its silence before it.

    ``cache`` keeps the audio of finished windows: if the allowance runs out half way, the retry
    (a later day, another model) reads only what is left instead of paying for it all again.
    ``progress(done, total)`` is called after each window.
    """
    pcm = bytearray()
    model: Optional[str] = None
    for index, window in enumerate(windows):
        if window.pause:
            pcm.extend(silence(window.pause))
        if cache is not None and index in cache:
            audio = cache[index]
        else:
            audio, model = synthesize_window(window, narration_style, dialogue_style, prefer=model)
            if cache is not None:
                cache[index] = audio
        pcm.extend(audio)
        if progress:
            progress(index + 1, len(windows))
    return pcm_to_mp3(bytes(pcm))
